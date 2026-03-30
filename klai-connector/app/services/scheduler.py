"""APScheduler integration for cron-based connector sync scheduling."""

import asyncio
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.connector import Connector
from app.models.sync_run import SyncRun

logger = get_logger(__name__)


class ConnectorScheduler:
    """Manages scheduled sync jobs for connectors using APScheduler.

    Each connector with a ``schedule`` (cron expression) gets a corresponding
    APScheduler job that triggers sync execution.
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
            sync_callback: Callable ``(connector_id: UUID, sync_run_id: UUID) -> Coroutine``
                to invoke when a scheduled sync fires. Typically ``SyncEngine.run_sync``.
        """
        self._sync_callback = sync_callback
        self._scheduler.start()

        async with session_maker() as session:
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

        Args:
            connector: Connector model with a non-null ``schedule`` field.
        """
        if not connector.schedule:
            return

        job_id = str(connector.id)
        try:
            self._scheduler.add_job(
                self._trigger_sync,
                trigger=CronTrigger.from_crontab(connector.schedule),
                id=job_id,
                args=[connector.id],
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

    async def _trigger_sync(self, connector_id: uuid.UUID) -> None:
        """Callback invoked by APScheduler to start a sync.

        Creates a SyncRun record and delegates to the sync engine.
        """
        from app.core.database import session_maker as db_session_maker

        if db_session_maker is None or self._sync_callback is None:
            logger.error("Cannot trigger scheduled sync: database or sync engine not initialised")
            return

        async with db_session_maker() as session:
            sync_run = SyncRun(connector_id=connector_id, status=SyncStatus.RUNNING)
            session.add(sync_run)
            await session.commit()
            await session.refresh(sync_run)

        asyncio.create_task(self._sync_callback(connector_id, sync_run.id))  # type: ignore[operator]
        logger.info("Scheduled sync triggered for connector %s", connector_id)

    async def shutdown(self) -> None:
        """Shut down the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
