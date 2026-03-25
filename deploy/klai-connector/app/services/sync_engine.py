"""Sync orchestrator with global semaphore and per-connector locking."""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from gidgethub import BadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.registry import AdapterRegistry
from app.adapters.webcrawler import CrawlJobPending
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.connector import Connector
from app.models.sync_run import SyncRun
from app.services.parser import parse_document

logger = get_logger(__name__)


class SyncEngine:
    """Orchestrates document sync from external sources to knowledge-ingest.

    Enforces:
    - Max 3 concurrent global syncs via :class:`asyncio.Semaphore`.
    - Max 1 sync per connector via per-connector locks.

    Args:
        session_maker: Async session factory for database access.
        registry: Adapter registry mapping connector types to adapters.
        ingest_client: Client for the knowledge-ingest service.
    """

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        registry: AdapterRegistry,
        ingest_client: KnowledgeIngestClient,
    ) -> None:
        self._session_maker = session_maker
        self._registry = registry
        self._ingest_client = ingest_client
        self._global_semaphore = asyncio.Semaphore(3)
        self._connector_locks: dict[uuid.UUID, asyncio.Lock] = {}

    def _get_connector_lock(self, connector_id: uuid.UUID) -> asyncio.Lock:
        """Get or create a per-connector lock."""
        if connector_id not in self._connector_locks:
            self._connector_locks[connector_id] = asyncio.Lock()
        return self._connector_locks[connector_id]

    async def run_sync(self, connector_id: uuid.UUID, sync_run_id: uuid.UUID) -> None:
        """Execute a full sync cycle for a connector.

        Designed to be called as a background task. Skips silently if a sync
        is already running for this connector (the route layer enforces 409,
        but the scheduler may race on restart).

        Args:
            connector_id: UUID of the connector to sync.
            sync_run_id: UUID of the pre-created SyncRun record.
        """
        lock = self._get_connector_lock(connector_id)
        if lock.locked():
            logger.warning("Sync already running for connector %s, skipping", connector_id)
            return

        async with lock, self._global_semaphore:
            await self._execute_sync(connector_id, sync_run_id)

    async def _execute_sync(self, connector_id: uuid.UUID, sync_run_id: uuid.UUID) -> None:
        """Internal sync execution with full error handling and metrics."""
        start_time = time.monotonic()
        documents_total = 0
        documents_ok = 0
        documents_failed = 0
        bytes_processed = 0
        error_details: list[dict[str, str]] = []

        async with self._session_maker() as session:
            connector = await session.get(Connector, connector_id)
            sync_run = await session.get(SyncRun, sync_run_id)
            if connector is None or sync_run is None:
                logger.error("Connector or SyncRun not found: %s / %s", connector_id, sync_run_id)
                return

            # Resolve the adapter for this connector type.
            try:
                adapter = self._registry.get(connector.connector_type)
            except ValueError:
                logger.error(
                    "Unsupported connector type %r for connector %s",
                    connector.connector_type, connector_id,
                )
                sync_run.status = SyncStatus.FAILED
                sync_run.completed_at = datetime.now(UTC)
                sync_run.error_details = [{"error": f"Unsupported connector type: {connector.connector_type!r}"}]
                connector.last_sync_at = datetime.now(UTC)
                connector.last_sync_status = SyncStatus.FAILED
                await session.commit()
                return

            status = SyncStatus.COMPLETED
            cursor_state: dict[str, Any] | None = None

            try:
                cursor_state = await adapter.get_cursor_state(connector)

                last_run = await self._get_last_successful_run(session, connector_id)

                # Also check for PENDING runs to resume async jobs.
                last_pending = await self._get_last_pending_run(session, connector_id)
                cursor_context: dict[str, Any] | None = None
                if last_pending and last_pending.cursor_state:
                    cursor_context = last_pending.cursor_state
                elif last_run and last_run.cursor_state:
                    cursor_context = last_run.cursor_state

                # Tree-SHA optimization for GitHub (safe: non-GitHub adapters
                # won't have tree_sha, so the check short-circuits).
                if last_run and last_run.cursor_state:
                    old_sha = last_run.cursor_state.get("tree_sha")
                    new_sha = cursor_state.get("tree_sha")
                    if old_sha and new_sha and old_sha == new_sha:
                        logger.info("No changes detected for connector %s, skipping sync", connector_id)
                        sync_run.status = SyncStatus.COMPLETED
                        sync_run.completed_at = datetime.now(UTC)
                        sync_run.cursor_state = cursor_state
                        await session.commit()
                        return

                refs = await adapter.list_documents(connector, cursor_context=cursor_context)
                documents_total = len(refs)

                config: dict[str, Any] = connector.config
                kb_slug: str = config.get("kb_slug", "org")

                for ref in refs:
                    try:
                        content_bytes = await adapter.fetch_document(ref, connector)
                        bytes_processed += len(content_bytes)
                        text = parse_document(content_bytes, ref.path.split("/")[-1])
                        await self._ingest_client.ingest_document(
                            org_id=str(connector.org_id),
                            kb_slug=kb_slug,
                            path=ref.path,
                            content=text,
                            source_connector_id=str(connector.id),
                            source_ref=ref.source_ref,
                        )
                        documents_ok += 1

                    except Exception as doc_err:
                        documents_failed += 1
                        error_details.append({"file": ref.path, "error": str(doc_err)})
                        logger.warning(
                            "Failed to process %s: %s", ref.path, doc_err,
                            extra={"connector_id": str(connector_id)},
                        )

            except CrawlJobPending as exc:
                # Async crawl job not finished yet: mark as PENDING so the
                # next scheduled sync resumes polling.
                sync_run.status = SyncStatus.PENDING
                sync_run.completed_at = datetime.now(UTC)
                sync_run.cursor_state = {
                    "pending_task_id": exc.task_id,
                    "job_started_at": exc.job_started_at,
                    "base_url": connector.config.get("base_url", ""),
                }
                connector.last_sync_at = datetime.now(UTC)
                connector.last_sync_status = SyncStatus.PENDING
                await session.commit()
                logger.info(
                    "Crawl job %s still pending for connector %s, will resume next sync",
                    exc.task_id, connector_id,
                )
                return

            except BadRequest as err:
                # gidgethub raises BadRequest for 401/403; treat as auth failure
                if err.status_code in (401, 403):
                    status = SyncStatus.AUTH_ERROR
                else:
                    status = SyncStatus.FAILED
                error_details.append({"error": str(err)})
                logger.exception("Sync failed for connector %s", connector_id,
                                  extra={"connector_id": str(connector_id)})

            except Exception as err:
                status = SyncStatus.FAILED
                error_details.append({"error": str(err)})
                logger.exception("Sync failed for connector %s", connector_id,
                                  extra={"connector_id": str(connector_id)})

            duration = time.monotonic() - start_time
            sync_run.status = status
            sync_run.completed_at = datetime.now(UTC)
            sync_run.documents_total = documents_total
            sync_run.documents_ok = documents_ok
            sync_run.documents_failed = documents_failed
            sync_run.bytes_processed = bytes_processed
            sync_run.error_details = error_details if error_details else None
            sync_run.cursor_state = cursor_state
            connector.last_sync_at = datetime.now(UTC)
            connector.last_sync_status = status

            await session.commit()

            logger.info(
                "Sync complete for connector %s",
                connector_id,
                extra={
                    "event": "sync_complete",
                    "connector_id": str(connector_id),
                    "duration_seconds": round(duration, 1),
                    "documents_total": documents_total,
                    "documents_ok": documents_ok,
                    "documents_failed": documents_failed,
                    "bytes_processed": bytes_processed,
                },
            )

    @staticmethod
    async def _get_last_successful_run(
        session: AsyncSession, connector_id: uuid.UUID
    ) -> SyncRun | None:
        """Retrieve the most recent successful sync run for a connector."""
        result = await session.execute(
            select(SyncRun)
            .where(
                SyncRun.connector_id == connector_id,
                SyncRun.status == SyncStatus.COMPLETED,
            )
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def _get_last_pending_run(
        session: AsyncSession, connector_id: uuid.UUID
    ) -> SyncRun | None:
        """Retrieve the most recent PENDING sync run for a connector."""
        result = await session.execute(
            select(SyncRun)
            .where(
                SyncRun.connector_id == connector_id,
                SyncRun.status == SyncStatus.PENDING,
            )
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
        return result.scalars().first()
