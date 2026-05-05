"""SPEC-CRAWLER-006 REQ-06: reaper for orphaned web_crawler sync_runs.

The :class:`SyncRunResolver` finalises ``RUNNING`` rows on read. That
covers the 99% case: anyone opening the connector page in the portal
triggers resolution. The 1% are runs nobody reads — abandoned
connectors, deleted users, stuck rows from a knowledge-ingest crash.

The reaper runs every :data:`_TICK_S` seconds and finalises any web_crawler
sync_run that has been ``RUNNING`` for more than :data:`_FINALIZE_AFTER_S`
seconds (default 24h) by polling knowledge-ingest one final time.

Outcomes:

- knowledge-ingest returns terminal: write final state to sync_run + portal callback.
- knowledge-ingest returns 404 (job_not_found): mark sync_run FAILED with
  ``error.error = 'remote_job_lost'``.
- knowledge-ingest returns running AND row started_at > :data:`_FORCE_FAIL_AFTER_S`
  (default 7 days): force-fail with ``error.error = 'remote_job_stuck'``.
- knowledge-ingest returns running AND row younger than 7d: leave row alone,
  retry on next tick.
- knowledge-ingest unreachable: leave row alone, retry on next tick.

The reaper does NOT call ``crawl_sync_cancel`` — knowledge-ingest's own
lifecycle is the source of truth on whether a job should still be alive.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.knowledge_ingest import CrawlSyncClient
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.services.portal_client import PortalClient

logger = get_logger(__name__)

# Tunable knobs. Reasonable defaults for production; tests override.
_TICK_S: float = 5 * 60.0
_FINALIZE_AFTER_S: float = 24 * 60 * 60.0
_FORCE_FAIL_AFTER_S: float = 7 * 24 * 60 * 60.0


class SyncRunReaper:
    """Background task that finalises orphan delegated sync_runs.

    Wired into the FastAPI lifespan: started before ``yield``, cancelled
    on shutdown. ``async_run`` blocks until cancelled; the lifespan
    handler does ``asyncio.create_task(reaper.async_run())`` so it does
    not block startup.
    """

    def __init__(
        self,
        *,
        crawl_sync_client: CrawlSyncClient,
        session_maker: async_sessionmaker[AsyncSession],
        portal_client: PortalClient,
        tick_seconds: float = _TICK_S,
        finalize_after_seconds: float = _FINALIZE_AFTER_S,
        force_fail_after_seconds: float = _FORCE_FAIL_AFTER_S,
    ) -> None:
        self._crawl_sync_client = crawl_sync_client
        self._session_maker = session_maker
        self._portal_client = portal_client
        self._tick = tick_seconds
        self._finalize_after = finalize_after_seconds
        self._force_fail_after = force_fail_after_seconds

    async def async_run(self) -> None:
        """Run forever, ticking at the configured interval.

        Cancellation (lifespan shutdown) propagates as
        :class:`asyncio.CancelledError` and exits cleanly.
        """
        logger.info(
            "sync_run_reaper_started",
            extra={
                "tick_seconds": self._tick,
                "finalize_after_seconds": self._finalize_after,
                "force_fail_after_seconds": self._force_fail_after,
            },
        )
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    # Never let one bad tick kill the reaper. Log + retry.
                    logger.exception("sync_run_reaper_tick_failed")
                await asyncio.sleep(self._tick)
        except asyncio.CancelledError:
            logger.info("sync_run_reaper_stopped")
            raise

    async def tick(self) -> int:
        """Run a single sweep. Returns the number of rows finalised.

        Public for testing — the test suite calls ``tick`` directly
        rather than starting the loop.
        """
        finalize_cutoff = datetime.now(UTC) - timedelta(seconds=self._finalize_after)
        force_fail_cutoff = datetime.now(UTC) - timedelta(seconds=self._force_fail_after)

        finalised = 0
        async with self._session_maker() as session:
            stmt = select(SyncRun).where(
                and_(
                    SyncRun.status == SyncStatus.RUNNING,
                    SyncRun.started_at < finalize_cutoff,
                    # Marker contract: only delegated runs (cursor_state.remote_job_id set).
                    SyncRun.cursor_state["remote_job_id"].astext.isnot(None),  # type: ignore[index]
                ),
            )
            rows = (await session.execute(stmt)).scalars().all()

        for row in rows:
            if await self._reap_row(row, force_fail_cutoff=force_fail_cutoff):
                finalised += 1

        if finalised:
            logger.info(
                "sync_run_reaper_tick_complete",
                extra={"finalised": finalised, "scanned": len(rows)},
            )
        return finalised

    async def _reap_row(self, row: SyncRun, *, force_fail_cutoff: datetime) -> bool:
        """Process a single candidate. Returns True if the row was finalised."""
        cursor = row.cursor_state if isinstance(row.cursor_state, dict) else {}
        remote_job_id = cursor.get("remote_job_id")
        if not remote_job_id:
            return False

        try:
            live = await self._crawl_sync_client.crawl_sync_status(str(remote_job_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # AC-06.2: job has disappeared upstream — mark as lost.
                await self._finalise_failed(
                    row,
                    error_code="remote_job_lost",
                    documents_total=0,
                    documents_ok=0,
                )
                return True
            logger.warning(
                "sync_run_reaper_upstream_status",
                extra={
                    "sync_run_id": str(row.id),
                    "status_code": exc.response.status_code,
                },
            )
            return False
        except httpx.HTTPError as exc:
            logger.warning(
                "sync_run_reaper_upstream_unreachable",
                extra={"sync_run_id": str(row.id), "error": str(exc)},
            )
            return False

        live_status = str(live.get("status", "running"))
        pages_total = int(live.get("pages_total") or 0)
        pages_done = int(live.get("pages_done") or 0)

        if live_status == "completed":
            await self._finalise_completed(row, pages_done=pages_done, pages_total=pages_total)
            return True
        if live_status == "failed":
            await self._finalise_failed(
                row,
                error_code=str(live.get("error") or "unknown"),
                documents_total=pages_total,
                documents_ok=pages_done,
            )
            return True

        # Still running. Force-fail if older than the hard ceiling.
        if row.started_at < force_fail_cutoff:
            await self._finalise_failed(
                row,
                error_code="remote_job_stuck",
                documents_total=pages_total,
                documents_ok=pages_done,
            )
            return True
        return False

    async def _finalise_completed(
        self, row: SyncRun, *, pages_done: int, pages_total: int,
    ) -> None:
        completed_at = datetime.now(UTC)
        async with self._session_maker() as session:
            # FOR UPDATE — see SyncRunResolver._finalize for the same
            # rationale (block concurrent finalisers; second reader
            # short-circuits on the post-commit non-RUNNING read).
            db_row = await session.get(SyncRun, row.id, with_for_update=True)
            if db_row is None or db_row.status != SyncStatus.RUNNING:
                return  # Race: resolver got there first.
            db_row.status = SyncStatus.COMPLETED
            db_row.completed_at = completed_at
            db_row.documents_total = pages_total
            db_row.documents_ok = pages_done
            db_row.documents_failed = 0
            db_row.error_details = None
            db_row.quality_status = "healthy"
            existing_cursor = db_row.cursor_state if isinstance(db_row.cursor_state, dict) else {}
            db_row.cursor_state = {**existing_cursor, "remote_status": "completed"}
            await session.commit()
        await self._portal_client.report_sync_status(
            connector_id=row.connector_id,
            sync_run_id=row.id,
            sync_status=SyncStatus.COMPLETED,
            completed_at=completed_at,
            documents_total=pages_total,
            documents_ok=pages_done,
            documents_failed=0,
            bytes_processed=0,
            error_details=None,
        )
        logger.info(
            "sync_run_reaper_finalised_completed",
            extra={
                "sync_run_id": str(row.id),
                "connector_id": str(row.connector_id),
                "documents_ok": pages_done,
            },
        )

    async def _finalise_failed(
        self,
        row: SyncRun,
        *,
        error_code: str,
        documents_total: int,
        documents_ok: int,
    ) -> None:
        completed_at = datetime.now(UTC)
        cursor = row.cursor_state if isinstance(row.cursor_state, dict) else {}
        remote_job_id = cursor.get("remote_job_id")
        error_details: list[dict[str, Any]] = [
            {
                "error": error_code,
                "service": "knowledge-ingest",
                "remote_job_id": str(remote_job_id) if remote_job_id else None,
            },
        ]
        async with self._session_maker() as session:
            db_row = await session.get(SyncRun, row.id, with_for_update=True)
            if db_row is None or db_row.status != SyncStatus.RUNNING:
                return
            db_row.status = SyncStatus.FAILED
            db_row.completed_at = completed_at
            db_row.documents_total = documents_total
            db_row.documents_ok = documents_ok
            db_row.documents_failed = max(0, documents_total - documents_ok)
            db_row.error_details = error_details
            db_row.quality_status = None
            existing_cursor = db_row.cursor_state if isinstance(db_row.cursor_state, dict) else {}
            db_row.cursor_state = {**existing_cursor, "remote_status": "failed"}
            await session.commit()
        await self._portal_client.report_sync_status(
            connector_id=row.connector_id,
            sync_run_id=row.id,
            sync_status=SyncStatus.FAILED,
            completed_at=completed_at,
            documents_total=documents_total,
            documents_ok=documents_ok,
            documents_failed=max(0, documents_total - documents_ok),
            bytes_processed=0,
            error_details=error_details,
        )
        logger.info(
            "sync_run_reaper_finalised_failed",
            extra={
                "sync_run_id": str(row.id),
                "connector_id": str(row.connector_id),
                "error": error_code,
            },
        )
