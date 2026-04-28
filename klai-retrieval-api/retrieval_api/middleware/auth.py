"""SPEC-SEC-010: Authentication + rate-limit middleware for retrieval-api.

Every request (except ``/health`` and ``/metrics``) MUST carry one of:

- ``X-Internal-Secret`` header matching ``settings.internal_secret`` (compared
  with ``hmac.compare_digest`` — never ``==``).
- ``Authorization: Bearer <jwt>`` where the JWT is a valid Zitadel access token
  for ``settings.zitadel_issuer`` with audience ``settings.zitadel_api_audience``.

When both credentials are present, the JWT path is preferred (stricter identity
checks apply via :func:`verify_body_identity`). When neither is present, the
request is rejected with HTTP 401.

Internal-secret callers are trusted service principals (portal-api, LiteLLM
knowledge hook). REQ-3.3 intentionally skips cross-user / cross-org checks for
this path — those callers have already resolved the caller's identity out of
band (portal-api via ``_get_caller_org``; LiteLLM via team-key metadata).

Rate limiting (REQ-4) runs after auth succeeds. Identity key:

- JWT path:      ``retrieval:rl:jwt:<sha256(sub)[:32]>``
- Internal path: ``retrieval:rl:internal:<source_ip>`` where source_ip is the
  first hop of ``X-Forwarded-For`` when present, else ``request.client.host``.

This middleware is FAIL-CLOSED: the service refuses to start without
``INTERNAL_SECRET`` (enforced in :mod:`retrieval_api.config`). The knowledge-
ingest ``InternalSecretMiddleware`` reference implementation (F-003) deliberately
skips auth when the secret is unset — that fail-open branch is NOT copied here.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError, jwt
from klai_identity_assert import KNOWN_CALLER_SERVICES, IdentityAsserter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from retrieval_api.config import settings
from retrieval_api.metrics import (
    auth_rejected_total,
    cross_org_rejected_total,
    cross_user_rejected_total,
    rate_limited_total,
)
from retrieval_api.services.rate_limit import check_and_increment

logger = structlog.get_logger(__name__)

# Paths exempt from auth, cross-user/org checks, and rate limiting (REQ-1.6, REQ-4.4).
# `/metrics` is Docker-intern only and scraped by Alloy — keeping it unauthenticated
# matches every other Klai service.
_UNAUTH_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})

_ZITADEL_RESOURCEOWNER_CLAIM = "urn:zitadel:iam:user:resourceowner:id"
_ZITADEL_ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"

# JWKS in-memory cache (REQ-NFR performance). Mirrors research-api pattern;
# refreshed on kid miss. A cold-cache outage yields 503, never silent fail-open.
_jwks_cache: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuthContext:
    """Represents the authenticated principal for the current request.

    method    -- "internal" or "jwt" (service principal vs. user principal).
    sub       -- JWT ``sub`` claim (user id) when method == "jwt", else None.
    resourceowner -- JWT Zitadel ``resourceowner`` claim (org id) when method == "jwt".
    role      -- Highest-privilege role name from the JWT (e.g. "admin"), or None /
                 "service" for internal callers. Used by REQ-3 admin bypass.
    """

    method: str
    sub: str | None
    resourceowner: str | None
    role: str | None


@dataclass(frozen=True, slots=True)
class VerifiedCaller:
    """Identity asserted on behalf of an end-user, verified end-to-end.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-4 + REQ-6: every retrieve / chat call
    that performs work on behalf of a specific user populates this on
    ``request.state.verified_caller``. Downstream code (emit_event, audit
    logs) sources tenant identity from here instead of from the request
    body — so a tampered body cannot poison product_events even if the
    middleware-level guard is ever weakened.

    For JWT callers the values come from ``auth.sub`` / ``auth.resourceowner``
    (already cryptographically verified). For internal-secret callers they
    come from a portal-api ``/internal/identity/verify`` round-trip.
    """

    user_id: str
    org_id: str


def _unauthorized(reason: str) -> Response:
    """Build a 401 response for auth rejections (REQ-1.2).

    Logs the rejection and increments the Prometheus counter. Never echoes
    caller-supplied values back in the body.
    """
    logger.warning("auth_rejected", reason=reason)
    try:
        auth_rejected_total.labels(reason=reason).inc()
    except Exception:
        logger.exception("auth_metric_increment_failed", reason=reason)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"error": "unauthorized"},
    )


def _rate_limited(retry_after: int, method: str) -> Response:
    logger.warning("rate_limit_exceeded", method=method, retry_after=retry_after)
    try:
        rate_limited_total.labels(method=method).inc()
    except Exception:
        logger.exception("rate_limit_metric_increment_failed", method=method)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": "rate_limit_exceeded"},
        headers={"Retry-After": str(retry_after)},
    )


def _hash_sub(sub: str) -> str:
    """SPEC-SEC-010 REQ-3.4 / REQ-7.1: truncated SHA-256 for log correlation.

    Never log plaintext ``sub`` / ``user_id`` / ``org_id`` — only their hashes.
    """
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()[:12]


def _constant_time_secret_match(provided: str | None, expected: str) -> bool:
    """Constant-time secret comparison (REQ-1.5).

    ``hmac.compare_digest`` is required in every secret-compare path. A literal
    ``==`` on secrets would leak length / timing information; this helper
    centralises the rule so callers cannot forget.
    """
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


async def _fetch_jwks() -> dict[str, Any]:
    import httpx  # local import keeps the middleware cheap to load at startup

    jwks_url = f"{settings.zitadel_issuer}/oauth/v2/keys"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks(force_refresh: bool = False) -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is None or force_refresh:
        _jwks_cache = await _fetch_jwks()
    return _jwks_cache


def _find_key(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    for k in jwks.get("keys", []):
        if kid is None or k.get("kid") == kid:
            return k
    return None


async def _decode_jwt(token: str) -> tuple[dict[str, Any], str | None]:
    """Decode and validate a Zitadel JWT.

    Returns
    -------
    (payload, error_reason):
        payload is populated on success; error_reason is None.
        On failure payload is empty and error_reason is one of:
        ``invalid_jwt_signature``, ``invalid_jwt_audience``, ``expired_jwt``.
    """
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
    except JWTError:
        return {}, "invalid_jwt_signature"

    try:
        jwks = await _get_jwks()
        key = _find_key(jwks, kid)
        if key is None:
            jwks = await _get_jwks(force_refresh=True)
            key = _find_key(jwks, kid)
        if key is None:
            return {}, "invalid_jwt_signature"
    except Exception:
        # JWKS unreachable — fail-closed. The service MUST NOT accept tokens we
        # cannot verify.
        logger.exception("jwks_unavailable")
        return {}, "invalid_jwt_signature"

    # SPEC-SEC-010 REQ-1.2: audience verification is MANDATORY (contrast with
    # research-api F-004 where ``verify_aud=False`` was opt-in). Startup config
    # validation in :mod:`retrieval_api.config` guarantees a non-empty audience.
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.zitadel_issuer,
            audience=settings.zitadel_api_audience,
        )
        return payload, None
    except ExpiredSignatureError:
        return {}, "expired_jwt"
    except JWTError as exc:
        # python-jose merges audience / issuer / signature failures into JWTError.
        msg = str(exc).lower()
        if "audience" in msg:
            return {}, "invalid_jwt_audience"
        return {}, "invalid_jwt_signature"


def _extract_role(payload: dict[str, Any]) -> str | None:
    """Extract a simple role label from the Zitadel role claim.

    Zitadel embeds roles as a nested dict under ``urn:zitadel:iam:org:project:roles``.
    We only need a coarse-grained classification (``admin`` vs. everything else)
    for REQ-3 admin bypass.
    """
    roles_claim = payload.get(_ZITADEL_ROLES_CLAIM)
    if isinstance(roles_claim, dict) and roles_claim:
        if "admin" in roles_claim or "org_admin" in roles_claim:
            return "admin"
        # First key is deterministic enough for log correlation.
        return next(iter(roles_claim))
    if isinstance(roles_claim, list) and roles_claim:
        if "admin" in roles_claim or "org_admin" in roles_claim:
            return "admin"
        return roles_claim[0]
    # Fallback: some token shapes put role directly on ``role``.
    role = payload.get("role")
    return role if isinstance(role, str) else None


def _source_ip(request: Request) -> str:
    # SPEC-SEC-WEBHOOK-001 REQ-1.5: trust boundary for rate-limit key derivation.
    # Previously this function read `X-Forwarded-For` directly from request headers,
    # bypassing uvicorn's `--proxy-headers` handling. That meant any klai-net peer
    # could forge an XFF value and either bypass the 600 rpm ceiling (by rotating
    # the forged IP per request) or collapse all traffic into the caller's TCP
    # peer bucket (denying others).
    #
    # After SPEC-SEC-WEBHOOK-001 REQ-1: retrieval-api's uvicorn runs with
    # `--proxy-headers --forwarded-allow-ips=127.0.0.1` — meaning NO upstream is
    # trusted to set X-Forwarded-For. `request.client.host` therefore always
    # reflects the TCP peer's container IP on klai-net (portal-api, litellm, etc.),
    # which is the legitimate caller identity for service-to-service rate-limiting.
    # We use it directly and NEVER read the raw header.
    if request.client is not None:
        return request.client.host
    return "unknown"


def _rate_limit_key(auth: AuthContext, request: Request) -> str:
    if auth.method == "jwt" and auth.sub:
        return f"retrieval:rl:jwt:{_hash_sub(auth.sub)}"
    return f"retrieval:rl:internal:{_source_ip(request)}"


class AuthMiddleware(BaseHTTPMiddleware):
    """Fail-closed auth + rate-limit middleware (SPEC-SEC-010 REQ-1, REQ-4).

    Placement: :func:`retrieval_api.main` adds this BEFORE ``RequestContextMiddleware``
    so that in Starlette's LIFO dispatch order, ``RequestContextMiddleware`` runs
    first (outermost) and binds ``request_id`` on the structlog context before
    this middleware emits its first log line (REQ-1.4, REQ-7.1).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # /health is an exact match; /metrics is app-mounted so any sub-path
        # (e.g. /metrics, /metrics/) MUST bypass auth too.
        if path in _UNAUTH_PATHS or path.startswith("/metrics"):
            return await call_next(request)

        internal_header = request.headers.get("x-internal-secret")
        auth_header = request.headers.get("authorization", "")
        bearer_token: str | None = None
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[len("Bearer ") :].strip() or None

        auth: AuthContext | None = None

        # REQ-1.3: prefer JWT path when both credentials are present.
        if bearer_token is not None:
            payload, error = await _decode_jwt(bearer_token)
            if error is not None:
                return _unauthorized(error)
            sub = payload.get("sub")
            resourceowner = payload.get(_ZITADEL_RESOURCEOWNER_CLAIM)
            if not sub:
                return _unauthorized("invalid_jwt_signature")
            auth = AuthContext(
                method="jwt",
                sub=str(sub),
                resourceowner=str(resourceowner) if resourceowner is not None else None,
                role=_extract_role(payload),
            )
        elif internal_header is not None:
            if not _constant_time_secret_match(internal_header, settings.internal_secret):
                return _unauthorized("invalid_internal_secret")
            auth = AuthContext(
                method="internal",
                sub=None,
                resourceowner=None,
                role="service",
            )
        else:
            return _unauthorized("missing_credentials")

        request.state.auth = auth

        # REQ-4: sliding-window rate limit per identity.
        allowed, retry_after = await check_and_increment(
            settings.redis_url,
            _rate_limit_key(auth, request),
            settings.rate_limit_rpm,
        )
        if not allowed:
            return _rate_limited(retry_after, auth.method)

        # REQ-7.1: log successful auth decision.
        logger.info(
            "auth_accepted",
            auth_method=auth.method,
            role=auth.role,
            path=request.url.path,
        )

        return await call_next(request)


# Module-level IdentityAsserter — REQ-4. Lazily instantiated on first use so
# tests that don't exercise the internal-secret verify path don't pay the cost
# of constructing an httpx.AsyncClient. Empty config raises at instantiation,
# which surfaces deploy/env mismatches loudly the first time a real internal
# call hits this guard.
_asserter: IdentityAsserter | None = None


def _get_asserter() -> IdentityAsserter:
    global _asserter
    if _asserter is None:
        _asserter = IdentityAsserter(
            portal_base_url=settings.portal_api_url,
            internal_secret=settings.portal_internal_secret,
        )
    return _asserter


def _identity_assertion_failed(reason: str) -> HTTPException:
    """Build a generic 403 for portal-side identity-assertion failures.

    REQ-4.4: never echo the portal's stable reason code to the caller —
    that information lives in logs, queryable as ``identity_assert_call``
    in VictoriaLogs.
    """
    cross_org_rejected_total.inc()
    logger.warning(
        "identity_assertion_failed",
        reason=reason,
    )
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "identity_assertion_failed"},
    )


async def verify_body_identity(
    request: Request, body_org_id: str, body_user_id: str | None
) -> None:
    """Cross-check caller identity against the request body, then pin the verified
    tuple on ``request.state.verified_caller`` for downstream consumers.

    Two paths, single contract:

    JWT (SPEC-SEC-010 REQ-3):
        * Skip the cross-check for ``admin`` role (REQ-3.1/3.2).
        * Reject body-vs-JWT mismatches with 403 ``org_mismatch`` /
          ``user_mismatch``.
        * On allow, pin ``VerifiedCaller(auth.sub, auth.resourceowner)``.

    Internal-secret (SPEC-SEC-IDENTITY-ASSERT-001 REQ-4):
        * REQ-4.2: call portal-api ``/internal/identity/verify`` with
          ``caller_service`` from the required ``X-Caller-Service`` header
          and ``(claimed_user_id, claimed_org_id)`` from the body.
        * Missing / unknown caller-service header → 400 ``missing_caller_service``
          (loud config error rather than silent fail-open).
        * Portal deny → 403 ``identity_assertion_failed`` (reason in logs).
        * On allow, pin ``VerifiedCaller`` from the portal response.

    Raises
    ------
    HTTPException(400)
        Internal-secret caller did not send ``X-Caller-Service`` or it is
        not in the library allowlist.
    HTTPException(403)
        JWT cross-check or portal verify rejected the call.
    """
    auth: AuthContext | None = getattr(request.state, "auth", None)
    if auth is None:
        # Auth middleware always runs before this; absence is a programming bug.
        return

    if auth.method == "jwt":
        if auth.role == "admin":
            # Admin bypass (REQ-3.1/3.2): admins legitimately act on other
            # users' tenants. We pin claim values rather than JWT values
            # so emit_event reflects the intended target tenant.
            if body_user_id is not None:
                request.state.verified_caller = VerifiedCaller(
                    user_id=str(body_user_id), org_id=str(body_org_id)
                )
            return

        if auth.resourceowner is not None and str(body_org_id) != str(auth.resourceowner):
            cross_org_rejected_total.inc()
            logger.warning(
                "cross_org_rejected",
                reason="org_mismatch",
                auth_method=auth.method,
                jwt_sub_hash=_hash_sub(auth.sub or ""),
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "org_mismatch"},
            )

        if body_user_id is not None and auth.sub is not None and str(body_user_id) != str(auth.sub):
            cross_user_rejected_total.inc()
            logger.warning(
                "cross_user_rejected",
                reason="user_mismatch",
                auth_method=auth.method,
                jwt_sub_hash=_hash_sub(auth.sub),
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "user_mismatch"},
            )

        # JWT path verified the (sub, resourceowner) tuple cryptographically;
        # body matched. Pin auth values as the verified identity.
        if auth.sub is not None and auth.resourceowner is not None:
            request.state.verified_caller = VerifiedCaller(
                user_id=auth.sub, org_id=auth.resourceowner
            )
        return

    # auth.method == "internal" — REQ-4.2 portal-side verification.
    if body_user_id is None:
        # Bodies that don't carry an end-user claim (admin/diagnostic style
        # calls that future code paths might add) are out of scope for the
        # verify-body-identity guard. Today every retrieve/chat caller MUST
        # supply user_id when the scope requires it (the route validates
        # this); hitting this branch means a different surface — leave the
        # verified tuple unpinned and let downstream raise if it tries to
        # read it.
        return

    caller_service = request.headers.get("x-caller-service", "").strip()
    if not caller_service:
        logger.warning(
            "missing_caller_service",
            path=request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_caller_service"},
        )
    if caller_service not in KNOWN_CALLER_SERVICES:
        logger.warning(
            "unknown_caller_service",
            caller_service=caller_service,
            path=request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_caller_service"},
        )

    asserter = _get_asserter()
    result = await asserter.verify(
        caller_service=caller_service,
        claimed_user_id=str(body_user_id),
        claimed_org_id=str(body_org_id),
        bearer_jwt=None,  # internal-secret path: no end-user JWT in the call
        request_headers=dict(request.headers),
    )
    if not result.verified or result.user_id is None or result.org_id is None:
        raise _identity_assertion_failed(result.reason or "unknown")

    request.state.verified_caller = VerifiedCaller(user_id=result.user_id, org_id=result.org_id)
