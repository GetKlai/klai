"""Keeps authenticated web-crawler sessions alive between scheduled syncs.

A cookie captured once via the connector wizard and replayed only at the
next scheduled crawl can go stale from server-side inactivity long before
that crawl runs — confirmed in production for the Voys/support RedCactus
connector: a manual re-sync 45 minutes after a fresh cookie paste succeeded
completely (0 pages walled), while every unattended daily run since then
failed identically. This ticks far more often than any connector's crawl
schedule and touches the stored session with one cheap request, so it
never goes idle before the next scheduled crawl needs it.
"""

from __future__ import annotations

import asyncio
import uuid

from app.clients.knowledge_ingest import CrawlSyncClient
from app.core.logging import get_logger
from app.services.portal_client import PortalClient, ScheduledConnector

logger = get_logger(__name__)

_TICK_S: float = 30 * 60.0  # 30 min — comfortably shorter than any observed session idle-timeout


class SessionKeepAlive:
    """Background task pinging every scheduled web_crawler connector with saved credentials."""

    def __init__(
        self,
        *,
        portal_client: PortalClient,
        crawl_sync_client: CrawlSyncClient,
        tick_seconds: float = _TICK_S,
    ) -> None:
        self._portal_client = portal_client
        self._crawl_sync_client = crawl_sync_client
        self._tick = tick_seconds
        # Last-known ok/not-ok state per connector. Purely a log-noise gate
        # (the ping still runs and result.ok is still trusted every tick) --
        # without it a persistently logged-out connector logs an error every
        # 30 minutes forever. Logs once on the failing transition, once on
        # recovery. Per-process: a restart re-logs the first tick, which is
        # fine (worst case one extra log line, not a repeat every tick).
        self._last_ok: dict[uuid.UUID, bool] = {}

    async def async_run(self) -> None:
        """Run forever, ticking at the configured interval.

        Cancellation (lifespan shutdown) propagates as
        :class:`asyncio.CancelledError` and exits cleanly.
        """
        logger.info("session_keepalive_started", extra={"tick_seconds": self._tick})
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    # Never let one bad tick kill the loop. Log + retry.
                    logger.exception("session_keepalive_tick_failed")
                await asyncio.sleep(self._tick)
        except asyncio.CancelledError:
            logger.info("session_keepalive_stopped")
            raise

    async def tick(self) -> int:
        """Ping every scheduled web_crawler connector with saved credentials.

        Public for testing (mirrors SyncRunReaper.tick()). Returns the
        number of connectors successfully pinged.
        """
        try:
            scheduled = await self._portal_client.list_scheduled_connectors()
        except Exception:
            logger.exception("session_keepalive_list_failed")
            return 0

        candidates = [item for item in scheduled if item.connector_type == "web_crawler" and item.has_saved_credentials]
        pinged = 0
        for item in candidates:
            if await self._ping(item):
                pinged += 1
        return pinged

    async def _ping(self, item: ScheduledConnector) -> bool:
        try:
            config = await self._portal_client.get_connector_config(item.connector_id)
        except Exception:
            logger.warning(
                "session_keepalive_config_failed",
                extra={"connector_id": str(item.connector_id)},
            )
            return False

        # Preference order: canary_url and discovery_seed_url are both
        # wizard-validated interior pages (proved reachable with cookies at
        # setup time); base_url is the site root and is the fallback of last
        # resort — on many CMSes it 302s to a language/landing path rather
        # than serving authenticated content directly.
        url = (
            config.config.get("canary_url") or config.config.get("discovery_seed_url") or config.config.get("base_url")
        )
        if not url:
            return False

        try:
            result = await self._crawl_sync_client.crawl_keepalive(
                connector_id=str(item.connector_id),
                org_id=item.org_id,
                url=url,
            )
        except Exception:
            logger.warning(
                "session_keepalive_ping_failed",
                extra={"connector_id": str(item.connector_id)},
            )
            return False

        if result.get("reason") == "no_saved_credentials":
            # The portal flags the connector as credentialed, but knowledge-ingest
            # finds no cookies to send: there is no session to keep alive, so this
            # is not a logged-out session and must not raise an error line.
            logger.info(
                "session_keepalive_no_stored_cookies",
                extra={"connector_id": str(item.connector_id)},
            )
            return True
        ok = bool(result.get("ok"))
        previous = self._last_ok.get(item.connector_id)
        self._last_ok[item.connector_id] = ok
        if not ok and previous is not False:
            logger.error(
                "session_keepalive_session_logged_out",
                extra={
                    "connector_id": str(item.connector_id),
                    "url": url,
                    "reason": result.get("reason"),
                },
            )
        elif ok and previous is False:
            logger.info(
                "session_keepalive_recovered",
                extra={"connector_id": str(item.connector_id), "url": url},
            )
        return True
