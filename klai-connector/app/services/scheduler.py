"""APScheduler integration for cron-based connector sync scheduling."""

import asyncio
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import cross_org_session, tenant_scoped_session
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.connector import Connector
from app.models.sync_run import SyncRun

logger = get_logger(__name__)


class ConnectorScheduler:
    """Manages scheduled sync jobs for connectors using APScheduler.

    Each connector with a ``schedule`` (cron expression) gets a corresponding
    APScheduler job that triggers sync execution.

    SPEC-SEC-CONNECTOR-RLS-001: every scheduled job carries the
    ``connector.org_id`` (Zitadel resourceowner string) through the
    APScheduler ``args`` so the trigger callback can bind RLS tenant
    context before INSERTing the SyncRun. The bootstrap that loads
    schedules at app startup is a legitimate cross-org operation and
    runs through ``cross_org_session()``.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._sync_callback: object | None = None

    async def start(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        sync_callback: object,
    ) -> None:
        """Start the scheduler and load all enabled connectors with schedules.

        Args:
            session_maker: Async session factory.
            sync_callback: Callable
                ``(connector_id: UUID, sync_run_id: UUID, org_id: str) -> Coroutine``
                to invoke when a scheduled sync fires. Typically
                ``SyncEngine.run_sync``.

        SPEC-SEC-CONNECTOR-RLS-001: the ``select(Connector)`` here loads
        schedules across all tenants — by definition a cross-org
        operation, run via ``cross_org_session()`` so the RLS policy
        permits the read. ``session_maker`` is kept on the signature
        for backward compatibility with existing callers / tests, but
        the bootstrap session itself comes from ``cross_org_session()``.
        """
        self._sync_callback = sync_callback
        self._scheduler.start()

        async with cross_org_session() as session:
            result = await session.execute(
                select(Connector).where(
                    Connector.is_enabled.is_(True),
                    Connector.schedule.isnot(None),
                )
            )
            connectors = result.scalars().all()
            for connector in connectors:
                self.add_job(connector)

        logger.info("Scheduler started with %d scheduled connectors", len(connectors))

    def add_job(self, connector: Connector) -> None:
        """Register a cron job for a connector.

        If a job already exists for this connector, it is replaced.

        SPEC-SEC-CONNECTOR-RLS-001: ``connector.org_id`` is registered
        as the second APScheduler arg so ``_trigger_sync`` can bind
        tenant context for the SyncRun INSERT. The Connector model's
        ``org_id`` is non-NULL (SPEC-SEC-TENANT-001 REQ-7.x) so this
        is always populated for any row that reaches the scheduler.
        """
        if not connector.schedule:
            return

        job_id = str(connector.id)
        try:
            self._scheduler.add_job(
                self._trigger_sync,
                trigger=CronTrigger.from_crontab(connector.schedule),
                id=job_id,
                args=[connector.id, connector.org_id],
                replace_existing=True,
            )
            logger.info("Scheduled job for connector %s: %s", connector.id, connector.schedule)
        except ValueError:
            logger.exception("Invalid cron expression for connector %s: %s", connector.id, connector.schedule)

    def remove_job(self, connector_id: uuid.UUID) -> None:
        """Remove the scheduled job for a connector.

        Args:
            connector_id: Connector UUID.
        """
        job_id = str(connector_id)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
            logger.info("Removed scheduled job for connector %s", connector_id)

    async def _trigger_sync(self, connector_id: uuid.UUID, org_id: str) -> None:
        """Callback invoked by APScheduler to start a sync.

        Creates a SyncRun record under the tenant's RLS context and
        delegates to the sync engine.

        Args:
            connector_id: Connector UUID. Forwarded to the sync engine.
            org_id: Zitadel-resourceowner string (the connector's tenant).
                Used to bind ``app.current_org_id`` for the SyncRun
                INSERT, and forwarded to the sync engine so its
                background work runs under the same tenant context.
        """
        if self._sync_callback is None:
            logger.error("Cannot trigger scheduled sync: sync engine not initialised")
            return

        async with tenant_scoped_session(org_id) as session:
            sync_run = SyncRun(
                connector_id=connector_id,
                org_id=org_id,
                status=SyncStatus.RUNNING,
            )
            session.add(sync_run)
            await session.commit()
            await session.refresh(sync_run)

        asyncio.create_task(  # type: ignore[operator]
            self._sync_callback(connector_id, sync_run.id, org_id),
        )
        logger.info(
            "Scheduled sync triggered for connector %s",
            connector_id,
            extra={"connector_id": str(connector_id), "org_id": org_id},
        )

    async def shutdown(self) -> None:
        """Shut down the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
