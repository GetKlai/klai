"""OAuth 2.0 Client Credentials token client for Klai inter-service auth.

SPEC-SEC-SERVICE-AUTH-001 REQ-2.

Design notes
------------

* Tokens are cached by the client. The cache holds the raw access_token string
  + an absolute expiry datetime computed from the IdP's ``expires_in``. The
  client refreshes proactively at 80% of the advertised TTL — for a default
  Zitadel 1h token, that gives a 48-min cache-hit window and a 12-min
  fail-recovery window if the IdP becomes briefly unavailable.

* A single ``asyncio.Lock`` serialises cache-miss token mints. Without this,
  N concurrent first-callers all race to mint, all see cache miss, all hit
  the IdP — needless load + potentially rate-limit violations.

* Empty / whitespace-only ``client_id`` or ``client_secret`` raises at
  construction time. There is intentionally NO silent fallback to legacy
  auth at this layer — callers that need a fallback (Phase C-1, REQ-5)
  implement it explicitly.

* Errors raised: ``ServiceAuthError`` is the only public error type. Wraps
  network errors, IdP errors, invalid response shapes. Callers catch this
  one class.

* The library does NOT validate or introspect tokens — it only mints them.
  Receiver-side validation lives in each service's auth middleware (Zitadel
  JWKS check + scope check).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class ServiceAuthError(Exception):
    """Raised when the token client cannot produce a valid access token.

    Wraps:
      * Network errors talking to the IdP token endpoint.
      * Non-2xx responses from the IdP (rejected credentials, scope errors).
      * Malformed token responses (missing access_token / expires_in).

    Callers decide how to handle: fail-closed (re-raise) or fall back to the
    legacy auth path during migration (SPEC-SEC-SERVICE-AUTH-001 REQ-5).
    """


# Refresh at 80% of advertised TTL. For Zitadel's 1h default that gives:
#   * Cache-hit window: 48 minutes
#   * Fail-recovery window: 12 minutes (IdP can be down for up to 12 min
#     before live calls start failing)
_REFRESH_FRACTION: float = 0.8

# Lower bound on TTL we'll accept from the IdP. Anything shorter is treated
# as a config error (someone misconfigured the token TTL on the service
# account in Zitadel) — we do NOT want to mint a fresh token every second.
_MIN_TTL_SECONDS: int = 60


class ZitadelTokenClient:
    """Async OAuth 2.0 Client Credentials token client.

    See ``ZitadelTokenClient.__init__`` for parameters.

    Thread/concurrency model: safe for concurrent ``get_token`` calls within
    a single event loop. Not designed for cross-loop sharing — each event
    loop should own its own instance.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        scope: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Construct a token client.

        Args:
            client_id: Zitadel service-account client ID. Empty / whitespace-only
                values raise ``ValueError``.
            client_secret: Zitadel service-account client secret. Same rule.
            token_url: Full URL to the IdP's token endpoint
                (e.g. ``https://auth.getklai.com/oauth/v2/token``). Must be HTTPS
                in production. Empty values raise ``ValueError``.
            scope: Optional space-separated list of scopes to request. The
                IdP grants the intersection of (requested scope) and
                (scopes assigned to the service account).
            timeout_seconds: HTTP timeout for the token endpoint request.

        Raises:
            ValueError: when client_id, client_secret, or token_url is empty
                or whitespace-only.
        """
        if not (client_id and client_id.strip()):
            raise ValueError("client_id must be non-empty")
        if not (client_secret and client_secret.strip()):
            raise ValueError("client_secret must be non-empty")
        if not (token_url and token_url.strip()):
            raise ValueError("token_url must be non-empty")

        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scope = scope
        self._timeout_seconds = timeout_seconds

        # Cache: (token_string, expires_at_utc). expires_at_utc is the wall-clock
        # time at which the token actually expires per the IdP. We refresh at
        # _REFRESH_FRACTION of the original TTL elapsed.
        self._cache: tuple[str, _dt.datetime] | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a valid access token, minting or refreshing as needed.

        Cache hit is logged once per cache-hit; cache miss + mint is logged
        with the resulting TTL. Concurrent callers serialise on
        ``self._lock`` so only one mint happens per cache-miss window.

        Raises:
            ServiceAuthError: token mint failed.
        """
        # Fast path: cache hit without taking the lock. asyncio assignment is
        # atomic per-statement, so reading the tuple ref here is safe.
        cached = self._cache
        if cached is not None and self._is_fresh(cached[1]):
            logger.debug("service_auth_token_cache_hit", client_id=self._client_id)
            return cached[0]

        # Slow path: take the lock, re-check (another coroutine may have minted
        # while we were waiting), mint if still stale.
        async with self._lock:
            cached = self._cache
            if cached is not None and self._is_fresh(cached[1]):
                logger.debug("service_auth_token_cache_hit", client_id=self._client_id)
                return cached[0]

            token, expires_at = await self._mint_token()
            self._cache = (token, expires_at)
            return token

    @staticmethod
    def _is_fresh(expires_at: _dt.datetime) -> bool:
        """Return True if the cached token is still within its refresh window.

        ``expires_at`` is the absolute wall-clock expiry. We treat any token
        within its refresh-window (80% of original TTL elapsed) as fresh.
        """
        return _dt.datetime.now(_dt.UTC) < expires_at

    async def _mint_token(self) -> tuple[str, _dt.datetime]:
        """Hit the IdP token endpoint and parse the response.

        Returns:
            (access_token, refresh_at_utc) — refresh_at is when we'll consider
            the cache stale (80% of TTL ahead from now).

        Raises:
            ServiceAuthError on any error condition.
        """
        body: dict[str, str] = {"grant_type": "client_credentials"}
        if self._scope:
            body["scope"] = self._scope

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                resp = await client.post(
                    self._token_url,
                    data=body,
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "service_auth_token_mint_failed",
                client_id=self._client_id,
                token_url=self._token_url,
                reason="http_error",
                error=str(exc),
            )
            raise ServiceAuthError(f"token mint network error: {exc}") from exc

        if resp.status_code != 200:
            # Trim the body so we don't log a megabyte of HTML on a misrouted call.
            body_excerpt = resp.text[:500] if resp.text else ""
            logger.warning(
                "service_auth_token_mint_failed",
                client_id=self._client_id,
                status_code=resp.status_code,
                reason="non_2xx",
                body_excerpt=body_excerpt,
            )
            raise ServiceAuthError(f"token mint rejected by IdP: {resp.status_code} {body_excerpt}")

        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            logger.warning(
                "service_auth_token_mint_failed",
                client_id=self._client_id,
                reason="malformed_json",
                error=str(exc),
            )
            raise ServiceAuthError(f"token endpoint returned non-JSON: {exc}") from exc

        access_token = payload.get("access_token")
        if not access_token or not isinstance(access_token, str):
            logger.warning(
                "service_auth_token_mint_failed",
                client_id=self._client_id,
                reason="missing_access_token",
            )
            raise ServiceAuthError("token response missing access_token")

        expires_in = payload.get("expires_in")
        if not isinstance(expires_in, int) or expires_in < _MIN_TTL_SECONDS:
            logger.warning(
                "service_auth_token_mint_failed",
                client_id=self._client_id,
                reason="invalid_expires_in",
                expires_in=expires_in,
            )
            raise ServiceAuthError(f"token response has invalid expires_in: {expires_in!r}")

        # Refresh at 80% of TTL: e.g. 1h token → cache valid for 48 min.
        refresh_at = _dt.datetime.now(_dt.UTC) + _dt.timedelta(
            seconds=int(expires_in * _REFRESH_FRACTION)
        )
        logger.info(
            "service_auth_token_minted",
            client_id=self._client_id,
            expires_in=expires_in,
            refresh_at=refresh_at.isoformat(),
        )
        return access_token, refresh_at
