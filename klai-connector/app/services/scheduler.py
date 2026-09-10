"""APScheduler integration for cron-based connector sync scheduling.

The portal is the single source of truth for schedules: the scheduler reads
``/internal/scheduled-connectors`` via PortalClient and re-fetches it
periodically, so schedule changes in the portal take effect without a
restart. The legacy ``connector.connectors`` table is not consulted.
"""

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.database import tenant_scoped_session
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.services.portal_client import PortalClient

logger = get_logger(__name__)

SyncCallback = Callable[[uuid.UUID, uuid.UUID], Coroutine[Any, Any, None]]

# Job id of the periodic portal re-fetch; reconcile must never drop it.
REFRESH_JOB_ID = "portal-schedule-refresh"
REFRESH_INTERVAL_MINUTES = 5


class ConnectorScheduler:
    """Manages scheduled sync jobs for connectors using APScheduler.

    Each connector the portal reports with a ``schedule`` (cron expression)
    gets a corresponding APScheduler job that triggers sync execution.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._portal_client: PortalClient | None = None
        self._sync_callback: SyncCallback | None = None
        # Strong references to in-flight sync tasks: asyncio only keeps a weak
        # reference to tasks, so a fire-and-forget task can be garbage-collected
        # mid-sync. Discarded via done-callback when the task finishes.
        self._sync_tasks: set[asyncio.Task[None]] = set()

    async def start(self, portal_client: PortalClient, sync_callback: SyncCallback) -> None:
        """Start the scheduler and register jobs from the portal.

        Args:
            portal_client: Client used to fetch the scheduled-connector list.
            sync_callback: Callable ``(connector_id: UUID, sync_run_id: UUID) -> Coroutine``
                to invoke when a scheduled sync fires. Typically ``SyncEngine.run_sync``.
        """
        self._portal_client = portal_client
        self._sync_callback = sync_callback
        self._scheduler.start()
        # A portal outage here must not crash startup: refresh() logs and
        # keeps whatever is registered; the interval job retries.
        await self.refresh()

    async def refresh(self) -> None:
        """Reconcile scheduler jobs with the portal's scheduled connectors.

        New entries are added, changed cron expressions replaced, entries
        gone from the portal removed. The refresh job itself is never
        removed. On a portal failure the existing jobs stay untouched.
        """
        if self._portal_client is None:
            logger.error("Cannot refresh scheduled connectors: portal client not initialised")
            return

        # (Re-)arm the periodic re-fetch first so a failed fetch still retries.
        self._scheduler.add_job(
            self.refresh,
            trigger=IntervalTrigger(minutes=REFRESH_INTERVAL_MINUTES),
            id=REFRESH_JOB_ID,
            replace_existing=True,
        )

        try:
            scheduled = await self._portal_client.list_scheduled_connectors()
        except Exception:
            logger.exception(
                "Failed to fetch scheduled connectors from portal; keeping %d registered jobs",
                len(self._scheduler.get_jobs()),
            )
            return

        desired_ids: set[str] = set()
        for item in scheduled:
            job_id = str(item.connector_id)
            try:
                trigger = CronTrigger.from_crontab(item.schedule)
            except ValueError:
                logger.exception("Invalid cron expression for connector %s: %s", job_id, item.schedule)
                continue
            self._scheduler.add_job(
                self._trigger_sync,
                trigger=trigger,
                id=job_id,
                args=[item.connector_id, item.org_id],
                replace_existing=True,
            )
            desired_ids.add(job_id)

        for job in self._scheduler.get_jobs():
            if job.id == REFRESH_JOB_ID or job.id in desired_ids:
                continue
            self._scheduler.remove_job(job.id)
            logger.info("Removed scheduled job for connector %s (no longer scheduled in portal)", job.id)

        logger.info("Scheduler refreshed with %d scheduled connectors", len(desired_ids))

    async def _trigger_sync(self, connector_id: uuid.UUID, org_id: str) -> None:
        """Callback invoked by APScheduler to start a sync.

        Runs in the connector's tenant context (SPEC-TI-002: the RLS WITH
        CHECK on connector.sync_runs rejects an INSERT without org_id) and
        skips when a sync for this connector is already RUNNING. Creates a
        SyncRun record and delegates to the sync engine.
        """
        if self._sync_callback is None:
            logger.error("Cannot trigger scheduled sync: sync engine not initialised")
            return

        async with tenant_scoped_session(org_id) as session:
            result = await session.execute(
                select(SyncRun).where(
                    SyncRun.connector_id == connector_id,
                    SyncRun.status == SyncStatus.RUNNING,
                )
            )
            if result.scalars().first() is not None:
                logger.warning(
                    "Skipping scheduled sync for connector %s: a sync run is already RUNNING",
                    connector_id,
                )
                return

            sync_run = SyncRun(connector_id=connector_id, org_id=org_id, status=SyncStatus.RUNNING)
            session.add(sync_run)
            await session.commit()
            await session.refresh(sync_run)

        task = asyncio.create_task(self._sync_callback(connector_id, sync_run.id))
        self._sync_tasks.add(task)
        task.add_done_callback(self._sync_tasks.discard)
        logger.info("Scheduled sync triggered for connector %s (org %s)", connector_id, org_id)

    async def shutdown(self) -> None:
        """Shut down the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
