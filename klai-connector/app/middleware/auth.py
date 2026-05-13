"""Zitadel OIDC token introspection middleware with TTL cache.

SPEC-SEC-IDENTITY-ASSERT-003 REQ-2: org-resolution flows through
portal-api ``/internal/identity/verify`` instead of reading the JWT
``urn:zitadel:iam:user:resourceowner:id`` claim. The introspection
remains the authentication gate; portal becomes the authorization
authority for org-membership.
"""

import hashlib
import hmac
import time
from collections import OrderedDict
from typing import Any

import httpx
from klai_identity_assert import IdentityAsserter
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# SPEC-SEC-IDENTITY-ASSERT-003 REQ-2.8: lazy module-level IdentityAsserter
# singleton, mirroring the retrieval-api pattern. Constructed on first use
# so tests that don't exercise the verify path don't pay the cost of an
# httpx.AsyncClient. Settings are passed at construction time via the
# AuthMiddleware (which already takes Settings).
_asserter: IdentityAsserter | None = None


def _get_asserter(settings: Settings) -> IdentityAsserter:
    global _asserter
    if _asserter is None:
        _asserter = IdentityAsserter(
            portal_base_url=settings.portal_api_url,
            internal_secret=settings.portal_internal_secret,
        )
    return _asserter


def _audience_matches(claim: Any, expected: str) -> bool:  # noqa: ANN401
    """Return True when ``expected`` appears in the ``aud`` claim.

    Zitadel may return ``aud`` as a string or a list (RFC 7519 §4.1.3). Handle both.
    """
    if isinstance(claim, str):
        return claim == expected
    if isinstance(claim, list):
        return expected in claim
    return False


# TTL + LRU cache: {token_hash: (claims_dict, expiry_timestamp)}
# Ordered by recency of use — the LRU entry is at the head, MRU at the tail.
_token_cache: "OrderedDict[str, tuple[dict[str, Any], float]]" = OrderedDict()
_CACHE_MAX_SIZE = 1000
_CACHE_TTL = 300  # 5 minutes


def _cache_get(token_hash: str) -> dict[str, Any] | None:
    """Look up a cached introspection result. Returns claims or None.

    On a hit, promotes the entry to the most-recently-used end so it is not
    the next candidate for LRU eviction.
    """
    entry = _token_cache.get(token_hash)
    if entry is None:
        return None
    claims, expiry = entry
    if time.monotonic() > expiry:
        _token_cache.pop(token_hash, None)
        return None
    # Mark as most-recently-used.
    _token_cache.move_to_end(token_hash)
    return claims


def _cache_put(token_hash: str, claims: dict[str, Any]) -> None:
    """Store an introspection result in the cache with LRU semantics.

    If the key already exists, the entry is overwritten and promoted to MRU.
    If the cache is full with a new key, the least-recently-used entry is
    evicted (not the least-recently-inserted).
    """
    if token_hash in _token_cache:
        # Overwrite existing entry: promote then reassign so the new claims
        # sit at the MRU end.
        _token_cache.move_to_end(token_hash)
    elif len(_token_cache) >= _CACHE_MAX_SIZE:
        # Evict least-recently-used entry (head of the OrderedDict).
        _token_cache.popitem(last=False)
    _token_cache[token_hash] = (claims, time.monotonic() + _CACHE_TTL)


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware for Zitadel OIDC token introspection.

    - Extracts Bearer token from ``Authorization`` header.
    - Posts to the Zitadel introspection endpoint.
    - Caches valid results for 5 minutes.
    - Attaches ``org_id`` to ``request.state``.
    - Excludes ``/health`` from authentication.
    """

    def __init__(self, app: Any, settings: Settings) -> None:  # noqa: ANN401
        super().__init__(app)
        self._introspection_url = settings.zitadel_introspection_url
        self._client_id = settings.zitadel_client_id
        self._client_secret = settings.zitadel_client_secret
        self._portal_secret = settings.portal_caller_secret
        # SPEC-SEC-AUDIT-2026-04 B2: Settings._require_zitadel_api_audience
        # guarantees this is non-empty at startup (fail-closed). The conditional
        # warn-only fallback that allowed empty audience has been removed.
        self._expected_audience = settings.zitadel_api_audience
        # SPEC-SEC-IDENTITY-ASSERT-003 REQ-2.8: hold the Settings instance so
        # _get_asserter can construct the singleton lazily on first verify call.
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process the request through authentication."""
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        token = auth_header[7:]

        # Portal service-to-service calls bypass Zitadel introspection.
        # Portal is the control plane and is the only caller with this secret.
        # SPEC-SEC-008 F-017: use constant-time comparison to remove the narrow
        # timing side-channel on the non-constant-time `==` operator. The
        # `self._portal_secret` null-check keeps the bypass fail-closed when the
        # env var is unset (empty string would otherwise match an empty token).
        if self._portal_secret and hmac.compare_digest(token.encode("utf-8"), self._portal_secret.encode("utf-8")):
            request.state.from_portal = True
            request.state.org_id = None  # no user org in portal calls
            return await call_next(request)

        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # Check cache
        claims = _cache_get(token_hash)
        if claims is None:
            claims = await self._introspect(token)
            if claims is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            # SPEC-SEC-008 F-017 / SPEC-SEC-AUDIT-2026-04 B2: verify `aud` BEFORE
            # writing to cache so a wrong-audience token is never cached as valid.
            # The audience is always non-empty (guaranteed by Settings validator).
            if not _audience_matches(claims.get("aud"), self._expected_audience):
                logger.warning(
                    "Rejecting token with unexpected audience",
                    extra={"expected_aud": self._expected_audience},
                )
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            _cache_put(token_hash, claims)

        # SPEC-SEC-IDENTITY-ASSERT-003 REQ-2.1 + REQ-2.2: org-resolution
        # flows through portal /internal/identity/verify. The
        # urn:zitadel:iam:user:resourceowner:id claim is no longer read.
        # claimed_org_id sourced from X-Org-Id header (REQ-2.2 — symmetric
        # with retrieval-api JWT path).
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            logger.warning("Token introspection succeeded but sub claim is missing")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        header_org_id = request.headers.get("x-org-id", "").strip()
        if not header_org_id:
            # REQ-2.3: loud config error rather than silent fail-open.
            logger.warning("Missing X-Org-Id header on JWT path", extra={"path": request.url.path})
            return JSONResponse({"error": "missing_org_id"}, status_code=400)

        asserter = _get_asserter(self._settings)
        result = await asserter.verify(
            caller_service="klai-connector",
            claimed_user_id=sub,
            claimed_org_id=header_org_id,
            bearer_jwt=token,
            request_headers=dict(request.headers),
        )
        if not result.verified:
            # REQ-2.4: 403 not 401 — the user has a valid Zitadel token but
            # no membership for the claimed org; that is an authorization
            # failure, not authentication.
            logger.warning(
                "identity_assertion_failed",
                extra={"reason": result.reason, "path": request.url.path},
            )
            return JSONResponse({"error": "identity_assertion_failed"}, status_code=403)

        # REQ-2.5: pin the portal-resolved org_id, NOT the JWT claim.
        request.state.org_id = str(result.org_id) if result.org_id else header_org_id
        return await call_next(request)

    async def _introspect(self, token: str) -> dict[str, Any] | None:
        """Perform token introspection against Zitadel.

        Args:
            token: Bearer token to introspect.

        Returns:
            Claims dictionary if token is active, else None.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._introspection_url,
                    data={"token": token},
                    auth=(self._client_id, self._client_secret),
                )
            if response.status_code != 200:
                logger.warning("Introspection returned status %d", response.status_code)
                return None

            data = response.json()
            if not data.get("active", False):
                return None
            return data  # type: ignore[no-any-return]
        except httpx.HTTPError:
            logger.exception("Token introspection request failed")
            return None
