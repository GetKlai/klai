"""Zitadel OIDC token introspection middleware with TTL cache."""

import hashlib
import time
import uuid
from typing import Any

# Namespace UUID for deterministic org_id conversion from Zitadel numeric IDs
_ORG_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

import httpx
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Simple TTL cache: {token_hash: (claims_dict, expiry_timestamp)}
_token_cache: dict[str, tuple[dict[str, Any], float]] = {}
_CACHE_MAX_SIZE = 1000
_CACHE_TTL = 300  # 5 minutes


def _cache_get(token_hash: str) -> dict[str, Any] | None:
    """Look up a cached introspection result. Returns claims or None."""
    entry = _token_cache.get(token_hash)
    if entry is None:
        return None
    claims, expiry = entry
    if time.monotonic() > expiry:
        _token_cache.pop(token_hash, None)
        return None
    return claims


def _cache_put(token_hash: str, claims: dict[str, Any]) -> None:
    """Store an introspection result in the cache."""
    # Evict oldest entries when cache is full
    if len(_token_cache) >= _CACHE_MAX_SIZE:
        oldest_key = next(iter(_token_cache))
        _token_cache.pop(oldest_key, None)
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

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process the request through authentication."""
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        token = auth_header[7:]
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # Check cache
        claims = _cache_get(token_hash)
        if claims is None:
            claims = await self._introspect(token)
            if claims is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            _cache_put(token_hash, claims)

        # Extract org_id from Zitadel's resourceowner claim and convert to UUID
        zitadel_org_id = claims.get("urn:zitadel:iam:user:resourceowner:id")
        if zitadel_org_id is None:
            logger.warning("Token introspection succeeded but resourceowner:id claim is missing")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        request.state.org_id = str(uuid.uuid5(_ORG_NS, str(zitadel_org_id)))
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
