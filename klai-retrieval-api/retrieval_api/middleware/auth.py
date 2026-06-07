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
knowledge hook). Their body identity claims are verified via portal-api before
downstream handlers may use them for product events or retrieval decisions.

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
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError, jwt
from klai_identity_assert import KNOWN_CALLER_SERVICES, IdentityAsserter
from klai_service_auth import project_role_scopes
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

# SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.2: the
# `urn:zitadel:iam:user:resourceowner:id` claim is no longer consulted.
# Klai BFF never requests the scope that emits it (see SPEC-002 §1).
# Authoritative org-resolution flows through portal /internal/identity/verify
# which membership-checks the Zitadel sub against portal_users. CI rule
# `rules/no-zitadel-resourceowner-claim.yml` blocks reintroduction.
_ZITADEL_ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"

# JWKS in-memory cache (REQ-NFR performance). Mirrors research-api pattern;
# refreshed on kid miss. A cold-cache outage yields 503, never silent fail-open.
_jwks_cache: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuthContext:
    """Represents the authenticated principal for the current request.

    method        -- "internal" or "jwt" (service principal vs. user principal).
    sub           -- JWT ``sub`` claim (user id) when method == "jwt", else None.
    role          -- Highest-privilege role name from the JWT (e.g. "admin"), or
                     None / "service" for internal callers. Used by REQ-3 admin
                     bypass.
    scopes        -- SPEC-SEC-SERVICE-AUTH-001 REQ-3: parsed OAuth 2.0 ``scope``
                     claim (space-separated string in the JWT) split into a
                     frozenset of individual scopes. Empty for internal-secret
                     callers (legacy path); they get a Phase B/C migration
                     bypass via ``require_scope``.
    bearer_token  -- The raw JWT string when method == "jwt", else None.
                     Carried so :func:`verify_body_identity` can forward it to
                     portal `/internal/identity/verify` for membership-side
                     org resolution (SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.3).
    """

    method: str
    sub: str | None
    role: str | None
    scopes: frozenset[str] = field(default_factory=frozenset)
    bearer_token: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedCaller:
    """Identity asserted on behalf of an end-user, verified end-to-end.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-4 + REQ-6: every retrieve / chat call
    that performs work on behalf of a specific user populates this on
    ``request.state.verified_caller``. Downstream code (emit_event, audit
    logs) sources tenant identity from here instead of from the request
    body — so a tampered body cannot poison product_events even if the
    middleware-level guard is ever weakened.

    Both JWT and internal-secret callers now go through portal-api
    ``/internal/identity/verify`` for org-resolution
    (SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.3): the JWT path passes
    ``bearer_jwt`` so portal can validate the signature, and the
    canonical org_id flows back through ``VerifiedCaller`` rather than
    being lifted from a JWT-side claim.
    """

    user_id: str
    org_id: str


@dataclass(frozen=True, slots=True)
class VerifiedTenant:
    """Tenant-only identity verified via portal-api.

    Used for service-to-service retrieval calls that carry an org claim but no
    end-user claim. Kept separate from ``VerifiedCaller`` so user-bound product
    events cannot accidentally source a tenant-only decision as if it had a
    user.
    """

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
    # SPEC-SEC-HYGIENE-001 REQ-44.3: cap the JWKS fetch timeout at 3 s
    # (down from 10 s). Zitadel's JWKS endpoint responds sub-second; a
    # 10 s ceiling left workers exposed to slow-loris on the JWKS host
    # for longer than necessary.
    async with httpx.AsyncClient(timeout=3.0) as client:
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
    for REQ-3 admin bypass in :func:`verify_body_identity`.

    SPEC-SEC-TENANT-001 REQ-4.1 (v0.5.0):
        ``"org_admin"`` is removed from the admin-equivalent set. It was never
        produced by any production flow in the monorepo (signup.py, users.py,
        invite_user, migrate-user-to-portal-org.sh all grant ``"org:owner"``),
        and no portal-invite path under the v0.5.0 mapping can reach it.

        ``"admin"`` is retained as admin-equivalent: it is not produced by any
        production flow either, but it is the keyed shape that the
        SPEC-SEC-010 / SPEC-SEC-TENANT-001 test fixtures use to assert the
        admin-bypass mechanism still functions. Removing it would require a
        coordinated test-fixture migration; that work belongs to
        SPEC-SEC-IDENTITY-ASSERT-001 (gamma direction), where the JWT-claim
        admin-bypass itself migrates to a portal-signed assertion.

        Crucially, ``"org:owner"`` is intentionally NOT in this set even
        though it IS reachable via the v0.5.0 admin invite flow. Adding it
        would re-introduce finding #10 in a more direct form: every
        signup-created or admin-invited user would gain the cross-org
        bypass. See ``.claude/rules/klai/platform/zitadel.md`` "Project
        roles and JWT claims" for the canonical authority model.
    """
    roles_claim = payload.get(_ZITADEL_ROLES_CLAIM)
    if isinstance(roles_claim, dict) and roles_claim:
        if "admin" in roles_claim:
            return "admin"
        # First key is deterministic enough for log correlation.
        return next(iter(roles_claim))
    if isinstance(roles_claim, list) and roles_claim:
        if "admin" in roles_claim:
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
            if not sub:
                return _unauthorized("invalid_jwt_signature")
            # SPEC-SEC-SERVICE-AUTH-001 REQ-3 + SPEC-SEC-SERVICE-AUTH-002 REQ-4b:
            # authorization scopes come from two places. (a) The RFC 6749 §3.3
            # space-separated ``scope`` claim. (b) Zitadel project-role claims
            # (``urn:zitadel:iam:org:project:<projectId>:roles``) — machine
            # tokens requesting the reserved ``…:projects:roles`` scope carry
            # the granted role keys ONLY there, never in ``scope``.
            # Missing both → empty set → endpoints with require_scope reject.
            scopes_claim = payload.get("scope") or ""
            scopes = frozenset(
                {s for s in str(scopes_claim).split() if s}
                | project_role_scopes(payload, settings.zitadel_api_audience)
            )
            # SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.1: do NOT lift
            # `urn:zitadel:iam:user:resourceowner:id` from the JWT — the
            # claim is unreliable per Klai's zitadel.md rule. Org-resolution
            # is delegated to portal /internal/identity/verify in
            # verify_body_identity (REQ-1.3). The bearer_token is carried
            # on AuthContext so the verify call can pass it along.
            auth = AuthContext(
                method="jwt",
                sub=str(sub),
                role=_extract_role(payload),
                scopes=scopes,
                bearer_token=bearer_token,
            )
        elif internal_header is not None:
            if not _constant_time_secret_match(internal_header, settings.internal_secret):
                return _unauthorized("invalid_internal_secret")
            auth = AuthContext(
                method="internal",
                sub=None,
                role="service",
                scopes=frozenset(),
                bearer_token=None,
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
        # SPEC-SEC-SERVICE-AUTH-001 REQ-4: include ``auth_path`` so a Grafana
        # panel can track migration progress (jwt vs internal_secret) per
        # service. Renamed from auth_method for clarity in the panel name.
        logger.info(
            "auth_accepted",
            auth_method=auth.method,
            auth_path=auth.method,
            sub=auth.sub,
            scopes=sorted(auth.scopes) if auth.scopes else None,
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


def _caller_service_or_400(request: Request) -> str:
    """Read and validate X-Caller-Service for internal-secret callers."""

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
    return caller_service


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
            # users' tenants. We pin claim values rather than JWT values so
            # emit_event reflects the intended target tenant. Org-scope admin
            # calls may omit user_id; those pin a tenant-only identity.
            if body_user_id is not None:
                request.state.verified_caller = VerifiedCaller(
                    user_id=str(body_user_id), org_id=str(body_org_id)
                )
            else:
                request.state.verified_tenant = VerifiedTenant(org_id=str(body_org_id))
            return

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

        # SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.3: resolve and verify the
        # JWT-bound caller's org via portal /internal/identity/verify.
        # claimed_org_id comes from the inbound X-Org-Id header (set by
        # LibreChat hook / knowledge-mcp proxy / docs-app); portal-side
        # membership lookup is authoritative.
        header_org_id = request.headers.get("x-org-id", "").strip()
        if not header_org_id:
            # REQ-1.4: loud config error rather than silent fail-open.
            logger.warning("missing_org_id", path=request.url.path)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "missing_org_id"},
            )

        if auth.sub is None or auth.bearer_token is None:
            # Programming bug: dispatch always populates these for JWT path.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "internal_auth_state"},
            )

        asserter = _get_asserter()
        result = await asserter.verify(
            caller_service="retrieval-api",
            claimed_user_id=auth.sub,
            claimed_org_id=header_org_id,
            bearer_jwt=auth.bearer_token,
            request_headers=dict(request.headers),
        )
        if not result.verified:
            raise _identity_assertion_failed(result.reason or "unknown")

        # REQ-1.5 defence-in-depth: the body's org_id MUST match the
        # portal-verified org. A mismatch means the body was forged
        # against a JWT for a different tenant.
        if str(body_org_id) != str(result.org_id):
            cross_org_rejected_total.inc()
            logger.warning(
                "cross_org_rejected",
                reason="org_mismatch",
                auth_method=auth.method,
                jwt_sub_hash=_hash_sub(auth.sub),
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "org_mismatch"},
            )

        # Pin the portal-verified identity. Downstream emit_event /
        # audit logs source from request.state.verified_caller.
        request.state.verified_caller = VerifiedCaller(
            user_id=str(result.user_id) if result.user_id else auth.sub,
            org_id=str(result.org_id) if result.org_id else header_org_id,
        )
        return

    # auth.method == "internal" — REQ-4.2 portal-side verification.
    if body_user_id is None:
        # Tenant-only service-to-service call. This path is deliberately
        # separate from the user-bound verify() call so a missing user_id can
        # never silently become a user assertion.
        caller_service = _caller_service_or_400(request)
        asserter = _get_asserter()
        result = await asserter.verify_tenant(
            caller_service=caller_service,
            claimed_org_id=str(body_org_id),
            request_headers=dict(request.headers),
        )
        if not result.verified or result.org_id is None:
            raise _identity_assertion_failed(result.reason or "unknown")

        request.state.verified_tenant = VerifiedTenant(org_id=result.org_id)
        return

    caller_service = _caller_service_or_400(request)

    # F2 fix-forward (retrieval coupling audit 2026-05-06): synthetic
    # `partner:<key_id>` identities go through the SAME portal verify path
    # as every other internal-secret caller. Portal-side identity_verifier
    # has a dedicated branch that resolves the key against partner_api_keys
    # and confirms the key's owning org matches the claim — so a forged
    # body claiming `(partner:any-key, victim-tenant)` is denied at the
    # portal, not pinned by retrieval-api. The earlier in-process bypass
    # was removed because it weakened defense-in-depth: an attacker
    # holding X-Internal-Secret could pin any (synthetic_user, any_org)
    # tuple as verified_caller and read the org's data.
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


# --- SPEC-SEC-SERVICE-AUTH-001 — scope-based authorization ------------------


def require_scope(scope: str):
    """FastAPI dependency factory: require a specific OAuth scope claim.

    SPEC-SEC-SERVICE-AUTH-001 REQ-3. Use as ``Depends(require_scope(...))``
    on routes that gate on a scope.

    During Phase B/C migration, internal-secret callers (``method="internal"``)
    bypass the scope check — they have full access by virtue of holding the
    shared secret. This bypass is removed in Phase D once all callers have
    migrated to JWT auth and the X-Internal-Secret middleware path is deleted.

    Returns:
        ``AuthContext`` of the calling principal — useful for downstream
        identity-based logic (e.g. logging caller ``sub``).

    Raises:
        HTTPException(403, "insufficient_scope") when method=="jwt" and the
        required scope is not present in the token.
    """

    async def _dep(request: Request) -> AuthContext:
        auth: AuthContext | None = getattr(request.state, "auth", None)
        if auth is None:
            # Means AuthMiddleware did not run (route on _UNAUTH_PATHS) or
            # was bypassed. Defensive — should not happen on real endpoints.
            raise HTTPException(status_code=401, detail="not_authenticated")

        # Phase B legacy bypass: internal-secret callers retain full access
        # until Phase D. Logged via auth_path=internal_secret in middleware
        # so observability can track the deprecated path.
        if auth.method == "internal":
            return auth

        if scope not in auth.scopes:
            logger.warning(
                "auth_scope_rejected",
                required_scope=scope,
                granted_scopes=sorted(auth.scopes),
                sub=auth.sub,
                path=request.url.path,
            )
            raise HTTPException(status_code=403, detail="insufficient_scope")

        return auth

    return _dep
