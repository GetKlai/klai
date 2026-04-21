"""Zitadel OIDC token introspection middleware with TTL cache."""

import hashlib
import hmac
import time
from collections import OrderedDict
from typing import Any

import httpx
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


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
        self._expected_audience = settings.zitadel_api_audience
        if not self._expected_audience:
            # SPEC-SEC-008 F-017 defense-in-depth: `aud` check falls back to
            # warn-only when the audience is unconfigured. Surface the gap at
            # startup so the warning is not lost in per-request noise.
            logger.warning(
                "zitadel_api_audience is empty — introspected tokens will NOT be audience-checked. "
                "Set ZITADEL_API_AUDIENCE for defense-in-depth (SPEC-SEC-008 F-017)."
            )

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
            # SPEC-SEC-008 F-017: verify `aud` BEFORE writing to cache so a
            # wrong-audience token is never cached as valid.
            if self._expected_audience and not _audience_matches(claims.get("aud"), self._expected_audience):
                logger.warning(
                    "Rejecting token with unexpected audience",
                    extra={"expected_aud": self._expected_audience},
                )
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            _cache_put(token_hash, claims)

        # Use raw Zitadel resourceowner ID — must match what knowledge-ingest uses
        zitadel_org_id = claims.get("urn:zitadel:iam:user:resourceowner:id")
        if zitadel_org_id is None:
            logger.warning("Token introspection succeeded but resourceowner:id claim is missing")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        request.state.org_id = str(zitadel_org_id)
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
