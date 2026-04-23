"""Shared base class for OAuth-backed connector adapters (SPEC-KB-025).

This module centralises the OAuth token refresh + in-memory caching logic
used by Google Drive (and, later, SharePoint). Adapters subclass
``OAuthAdapterBase`` and implement ``_refresh_oauth_token`` with the
provider-specific token endpoint call.

Design decisions:
- Token cache is keyed by ``connector.id`` and uses ``time.monotonic()`` for
  expiry checks (wall-clock changes cannot prematurely invalidate a token).
- Cache holds only ``(access_token, expires_at_monotonic)`` — never the
  refresh_token, which lives in the encrypted connector config.
- On successful refresh, we call ``portal_client.update_credentials`` so the
  portal re-encrypts and persists the new access_token + token_expiry.
  This is best-effort: a callback failure is logged but never propagates,
  since the next sync can always refresh again.
- NEVER log access_token / refresh_token values — only metadata.
"""

# @MX:ANCHOR: [AUTO] Shared OAuth refresh path for all OAuth-backed adapters.
# @MX:REASON: fan_in>=2 now (google_drive) and grows with every new OAuth provider.
#             Cache + writeback invariants must stay identical across providers.
# @MX:SPEC: SPEC-KB-025

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.portal_client import PortalClient

logger = get_logger(__name__)


# How many seconds before actual expiry we consider a token "expired" and
# trigger a refresh. Avoids racing API calls against wall-clock expiry.
_EXPIRY_SKEW_SECONDS = 60.0


class OAuthAdapterBase(ABC):
    """Base class contributing OAuth token management to adapters.

    Subclasses MUST implement ``_refresh_oauth_token`` to call the provider's
    token endpoint with ``grant_type=refresh_token`` and return the raw JSON
    response (must contain ``access_token`` and ``expires_in``).

    Args:
        settings: Application settings (used by subclasses to read provider
            client_id / client_secret).
        portal_client: PortalClient for writeback after a successful refresh.
    """

    def __init__(self, settings: Settings, portal_client: PortalClient) -> None:
        self._settings = settings
        self._portal_client = portal_client
        # connector_id -> (access_token, expires_at_monotonic)
        self._token_cache: dict[str, tuple[str, float]] = {}
        # Guards concurrent refreshes for the same connector.
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    # -- Subclass contract ---------------------------------------------------

    @abstractmethod
    async def _refresh_oauth_token(
        self, connector: Any, refresh_token: str,
    ) -> dict[str, Any]:
        """Call the provider's token endpoint with the refresh_token.

        Args:
            connector: The connector model (provides org_id + config context).
            refresh_token: The long-lived OAuth refresh token.

        Returns:
            Raw JSON dict from the token endpoint. MUST contain ``access_token``
            and ``expires_in`` keys. Provider-specific extras are ignored here.
        """

    # -- Public API ----------------------------------------------------------

    async def ensure_token(self, connector: Any) -> str:
        """Return a valid access_token, refreshing if the cached one has expired.

        Args:
            connector: Connector model with ``id`` and ``config`` attributes.

        Returns:
            A non-empty access_token string suitable for Bearer auth.

        Raises:
            ValueError: If the connector config lacks a refresh_token AND the
                cached access_token is expired (or absent).
        """
        connector_id = str(connector.id)
        cached = self._token_cache.get(connector_id)
        now = time.monotonic()
        if cached is not None:
            token, expires_at = cached
            if expires_at - now > _EXPIRY_SKEW_SECONDS:
                return token

        # Serialise refreshes for the same connector so we don't burn through
        # Google's refresh quota with a thundering-herd at startup.
        lock = self._refresh_locks.setdefault(connector_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock in case another coroutine just refreshed.
            cached = self._token_cache.get(connector_id)
            now = time.monotonic()
            if cached is not None and cached[1] - now > _EXPIRY_SKEW_SECONDS:
                return cached[0]

            current_config: dict[str, Any] = connector.config or {}
            refresh_token_value = current_config.get("refresh_token", "")
            refresh_token: str = refresh_token_value if isinstance(refresh_token_value, str) else ""
            if not refresh_token:
                raise ValueError(
                    "OAuth connector missing refresh_token — reconnect required "
                    f"(connector_id={connector_id})"
                )

            # @MX:NOTE: [AUTO] Never log refresh_token / access_token values.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.info(
                "Refreshing OAuth token (connector=%s, provider=%s)",
                connector_id,
                type(self).__name__,
            )
            payload = await self._refresh_oauth_token(connector, refresh_token)
            access_token = payload.get("access_token", "")
            expires_in = int(payload.get("expires_in", 3600))
            if not access_token:
                raise ValueError(
                    "OAuth refresh response missing access_token "
                    f"(connector_id={connector_id})"
                )

            self._token_cache[connector_id] = (
                access_token,
                time.monotonic() + float(expires_in),
            )

            # @MX:NOTE: Refresh-token rotation (SPEC-KB-MS-DOCS-001 R9.2).
            # Microsoft rotates refresh_tokens on every refresh; the old token
            # is invalidated after a grace window. We must (a) writeback the
            # new one so it survives restart, and (b) mutate connector.config
            # so subsequent refreshes within the same process use the new RT.
            rotated_raw = payload.get("refresh_token")
            rotated_refresh_token: str | None = rotated_raw if isinstance(rotated_raw, str) else None
            if rotated_refresh_token is not None and rotated_refresh_token == refresh_token:
                # Provider echoed the same RT — treat as no-rotation.
                rotated_refresh_token = None
            if rotated_refresh_token:
                if connector.config is None:
                    connector.config = {}
                # connector.config is typed Any at the ABC boundary; cast narrows for the write.
                cast("dict[str, Any]", connector.config)["refresh_token"] = rotated_refresh_token  # pyright: ignore[reportUnknownMemberType]

            token_expiry = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
            try:
                await self._portal_client.update_credentials(
                    connector_id=connector_id,
                    access_token=access_token,
                    token_expiry=token_expiry,
                    refresh_token=rotated_refresh_token,
                )
            except Exception:
                # update_credentials is already best-effort; this is a second
                # safety net so sync never dies on a writeback failure.
                logger.exception(
                    "Portal writeback raised unexpectedly (connector=%s)",
                    connector_id,
                )

            return access_token

    # -- Helpers -------------------------------------------------------------

    def _cache_token(
        self, connector_id: str, access_token: str, expires_in_seconds: float,
    ) -> None:
        """Populate the cache from a freshly-fetched token (testing aid)."""
        self._token_cache[connector_id] = (
            access_token,
            time.monotonic() + float(expires_in_seconds),
        )
