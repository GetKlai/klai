"""Sync orchestrator with global semaphore and per-connector locking."""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from gidgethub import BadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.registry import AdapterRegistry
from app.adapters.webcrawler import CrawlJobPendingError
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.services.image_utils import extract_markdown_image_urls, resolve_relative_url
from app.services.parser import parse_document_with_images
from app.services.portal_client import PortalClient
from app.services.s3_storage import ImageStore
from app.services.sync_images import download_and_upload_images

logger = get_logger(__name__)


class SyncEngine:
    """Orchestrates document sync from external sources to knowledge-ingest.

    Enforces:
    - Max 3 concurrent global syncs via :class:`asyncio.Semaphore`.
    - Max 1 sync per connector via per-connector locks.

    Config is fetched from the portal control plane at sync time, so portal is
    the single source of truth — no local config copy is required.

    Args:
        session_maker: Async session factory for database access.
        registry: Adapter registry mapping connector types to adapters.
        ingest_client: Client for the knowledge-ingest service.
        portal_client: Client for the portal control plane API.
    """

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        registry: AdapterRegistry,
        ingest_client: KnowledgeIngestClient,
        portal_client: PortalClient,
        image_store: ImageStore | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._registry = registry
        self._ingest_client = ingest_client
        self._portal_client = portal_client
        self._image_store = image_store
        self._image_http = httpx.AsyncClient(timeout=30.0) if image_store else None
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
            connector_id: UUID of the connector to sync (portal_connectors.id).
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

        # Fetch connector config from portal (single source of truth).
        try:
            portal_config = await self._portal_client.get_connector_config(connector_id)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Portal returned %d for connector %s config — connector may have been deleted",
                exc.response.status_code,
                connector_id,
            )
            await self._fail_sync_run(sync_run_id, f"Portal config fetch failed: HTTP {exc.response.status_code}")
            return
        except Exception:
            logger.exception("Failed to fetch connector config from portal for %s", connector_id)
            await self._fail_sync_run(sync_run_id, "Portal config fetch failed")
            return

        async with self._session_maker() as session:
            sync_run = await session.get(SyncRun, sync_run_id)
            if sync_run is None:
                logger.error("SyncRun not found: %s", sync_run_id)
                return

            # Resolve the adapter for this connector type.
            try:
                adapter = self._registry.get(portal_config.connector_type)
            except ValueError:
                logger.error(
                    "Unsupported connector type %r for connector %s",
                    portal_config.connector_type,
                    connector_id,
                )
                sync_run.status = SyncStatus.FAILED
                sync_run.completed_at = datetime.now(UTC)
                sync_run.error_details = [
                    {"error": f"Unsupported connector type: {portal_config.connector_type!r}"}
                ]
                await session.commit()
                await self._portal_client.report_sync_status(
                    connector_id=connector_id,
                    sync_run_id=sync_run_id,
                    sync_status=SyncStatus.FAILED,
                    completed_at=sync_run.completed_at,
                    documents_total=0,
                    documents_ok=0,
                    documents_failed=0,
                    bytes_processed=0,
                    error_details=sync_run.error_details,
                )
                return

            status = SyncStatus.COMPLETED
            cursor_state: dict[str, Any] | None = None
            refs: list = []  # all discovered refs (for cursor_state synced_refs)

            try:
                cursor_state = await adapter.get_cursor_state(portal_config)

                last_run = await self._get_last_successful_run(session, connector_id)
                last_pending = await self._get_last_pending_run(session, connector_id)

                # Resume state: refs already ingested in an interrupted run.
                resume_ingested_refs: set[str] = set()
                if last_pending and last_pending.cursor_state:
                    resume_ingested_refs = set(
                        last_pending.cursor_state.get("ingested_refs", [])
                    )
                    if resume_ingested_refs:
                        logger.info(
                            "Resuming interrupted sync for connector %s: %d refs already ingested, skipping",
                            connector_id,
                            len(resume_ingested_refs),
                        )

                # Previous sync state for reconciliation.
                prev_cursor = (last_run.cursor_state or {}) if last_run else {}
                prev_synced_refs: set[str] = set(prev_cursor.get("synced_refs", []))
                prev_synced_at: str = prev_cursor.get("last_synced_at", "")

                # Tree-SHA optimisation for GitHub (safe: non-GitHub adapters won't have tree_sha).
                if prev_cursor:
                    old_sha = prev_cursor.get("tree_sha")
                    new_sha = (cursor_state or {}).get("tree_sha")
                    if old_sha and new_sha and old_sha == new_sha:
                        logger.info("No changes detected for connector %s, skipping sync", connector_id)
                        sync_run.status = SyncStatus.COMPLETED
                        sync_run.completed_at = datetime.now(UTC)
                        sync_run.cursor_state = cursor_state
                        await session.commit()
                        await self._portal_client.report_sync_status(
                            connector_id=connector_id,
                            sync_run_id=sync_run_id,
                            sync_status=SyncStatus.COMPLETED,
                            completed_at=sync_run.completed_at,
                            documents_total=0,
                            documents_ok=0,
                            documents_failed=0,
                            bytes_processed=0,
                            error_details=None,
                        )
                        return

                # Discovery: always fetch ALL refs (adapter does no time filtering).
                # cursor_context still passed for adapters that use it (e.g. webcrawler).
                cursor_context = prev_cursor or None
                refs = await adapter.list_documents(portal_config, cursor_context=cursor_context)

                # Reconciliation: decide which refs need syncing.
                # - New: not in prev_synced_refs → always sync
                # - Changed: last_edited > prev_synced_at → re-sync
                # - Unchanged: skip (already indexed, not modified)
                documents_skipped = 0
                refs_to_sync: list = []
                for ref in refs:
                    ref_key = ref.source_ref or ref.path
                    if ref_key in resume_ingested_refs:
                        documents_ok += 1
                        continue
                    is_new = ref_key not in prev_synced_refs
                    is_changed = bool(ref.last_edited and ref.last_edited > prev_synced_at)
                    if is_new or is_changed or not prev_synced_at:
                        refs_to_sync.append(ref)
                    else:
                        documents_skipped += 1

                documents_total = len(refs_to_sync)
                if documents_skipped:
                    logger.info(
                        "Reconciliation for connector %s: %d to sync, %d unchanged (skipped), %d total discovered",
                        connector_id, len(refs_to_sync), documents_skipped, len(refs),
                    )

                for ref in refs_to_sync:
                    ref_key = ref.source_ref or ref.path

                    try:
                        content_bytes = await adapter.fetch_document(ref, portal_config)
                        bytes_processed += len(content_bytes)
                        parse_result = parse_document_with_images(
                            content_bytes, ref.path.split("/")[-1],
                        )
                        text = parse_result.text
                        if len(text.strip()) < 50:
                            logger.info(
                                "Skipping short document (path=%s, chars=%d)",
                                ref.path, len(text.strip()),
                            )
                            documents_ok += 1
                            resume_ingested_refs.add(ref_key)
                            continue

                        # Image extraction and upload (when Garage is configured).
                        image_urls: list[str] | None = None
                        if self._image_store and self._image_http:
                            image_urls = await self._extract_and_upload_images(
                                text=text,
                                parsed_images=parse_result.images,
                                ref=ref,
                                org_id=portal_config.zitadel_org_id,
                                kb_slug=portal_config.kb_slug,
                                connector_type=portal_config.connector_type,
                                connector_config=portal_config.config,
                                adapter=adapter,
                            ) or None  # Convert empty list to None

                        await self._ingest_client.ingest_document(
                            org_id=portal_config.zitadel_org_id,
                            kb_slug=portal_config.kb_slug,
                            path=ref.path,
                            content=text,
                            source_connector_id=str(connector_id),
                            source_ref=ref.source_ref,
                            source_url=ref.source_url,
                            content_type=ref.content_type,
                            allowed_assertion_modes=portal_config.allowed_assertion_modes,
                            image_urls=image_urls,
                        )
                        documents_ok += 1
                        resume_ingested_refs.add(ref_key)

                        # Checkpoint progress every 10 docs so a crash can resume mid-sync.
                        if documents_ok % 10 == 0:
                            sync_run.cursor_state = {"ingested_refs": list(resume_ingested_refs)}
                            await session.commit()

                    except Exception as doc_err:
                        documents_failed += 1
                        error_details.append({"file": ref.path, "error": str(doc_err)})
                        logger.warning(
                            "Failed to process %s: %s",
                            ref.path,
                            doc_err,
                            extra={"connector_id": str(connector_id)},
                        )

                await adapter.post_sync(portal_config)

            except CrawlJobPendingError as exc:
                # Async crawl job not finished yet: mark as PENDING so the
                # next scheduled sync resumes polling.
                sync_run.status = SyncStatus.PENDING
                sync_run.completed_at = datetime.now(UTC)
                sync_run.cursor_state = {
                    "pending_task_id": exc.task_id,
                    "job_started_at": exc.job_started_at,
                    "base_url": portal_config.config.get("base_url", ""),
                }
                await session.commit()
                await self._portal_client.report_sync_status(
                    connector_id=connector_id,
                    sync_run_id=sync_run_id,
                    sync_status=SyncStatus.PENDING,
                    completed_at=sync_run.completed_at,
                    documents_total=0,
                    documents_ok=0,
                    documents_failed=0,
                    bytes_processed=0,
                    error_details=None,
                )
                logger.info(
                    "Crawl job %s still pending for connector %s, will resume next sync",
                    exc.task_id,
                    connector_id,
                )
                return

            except BadRequest as err:
                # gidgethub raises BadRequest for 401/403; treat as auth failure
                status = SyncStatus.AUTH_ERROR if err.status_code in (401, 403) else SyncStatus.FAILED
                error_details.append({"error": str(err)})
                logger.exception(
                    "Sync failed for connector %s",
                    connector_id,
                    extra={"connector_id": str(connector_id)},
                )

            except Exception as err:
                status = SyncStatus.FAILED
                error_details.append({"error": str(err)})
                logger.exception(
                    "Sync failed for connector %s",
                    connector_id,
                    extra={"connector_id": str(connector_id)},
                )

            duration = time.monotonic() - start_time
            completed_at = datetime.now(UTC)
            sync_run.status = status
            sync_run.completed_at = completed_at
            sync_run.documents_total = documents_total
            sync_run.documents_ok = documents_ok
            sync_run.documents_failed = documents_failed
            sync_run.bytes_processed = bytes_processed
            sync_run.error_details = error_details if error_details else None
            # Store all discovered refs for reconciliation on the next sync.
            # This is the full set from the adapter — new refs appear here, deleted
            # refs disappear. The sync engine compares against this on the next run.
            if status == SyncStatus.COMPLETED and cursor_state is not None:
                failed_refs = {e.get("file", "") for e in error_details}
                cursor_state["synced_refs"] = sorted(
                    (r.source_ref or r.path) for r in refs
                    if (r.source_ref or r.path) not in failed_refs
                )
            sync_run.cursor_state = cursor_state
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

        # Report final status to portal (best-effort — errors are swallowed in portal_client).
        await self._portal_client.report_sync_status(
            connector_id=connector_id,
            sync_run_id=sync_run_id,
            sync_status=status,
            completed_at=completed_at,
            documents_total=documents_total,
            documents_ok=documents_ok,
            documents_failed=documents_failed,
            bytes_processed=bytes_processed,
            error_details=error_details if error_details else None,
        )

    async def _extract_and_upload_images(
        self,
        *,
        text: str,
        parsed_images: list[dict[str, str]],
        ref: Any,
        org_id: str,
        kb_slug: str,
        connector_type: str,
        connector_config: dict[str, Any],
        adapter: Any = None,
    ) -> list[str]:
        """Extract image URLs from document content and upload to S3.

        Resolves relative URLs based on the connector type and config.
        For Notion, also handles image block URLs cached by the adapter.
        """
        assert self._image_store is not None
        assert self._image_http is not None

        # Extract markdown image URLs from text content.
        raw_urls = extract_markdown_image_urls(text)

        # For Notion: also include image block URLs from the adapter cache.
        if connector_type == "notion" and adapter is not None:
            from app.adapters.notion import NotionAdapter

            if isinstance(adapter, NotionAdapter):
                for img_ref in adapter.get_cached_images(ref.ref):
                    raw_urls.append((img_ref.alt, img_ref.url))

        # For adapters that populate DocumentRef.images directly (e.g. webcrawler via
        # media["images"] from crawl4ai — bypasses PruningContentFilter stripping).
        if ref.images:
            for img_ref in ref.images:
                raw_urls.append((img_ref.alt, img_ref.url))

        # Resolve relative URLs based on connector type.
        resolved: list[tuple[str, str]] = []
        for alt, url in raw_urls:
            if connector_type == "github":
                owner = connector_config.get("repo_owner", "")
                repo = connector_config.get("repo_name", "")
                branch = connector_config.get("branch", "main")
                base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
                url = resolve_relative_url(url, base)
            elif connector_type == "webcrawler":
                url = resolve_relative_url(url, ref.source_ref or ref.source_url or "")
            resolved.append((alt, url))

        return await download_and_upload_images(
            image_urls=resolved,
            org_id=org_id,
            kb_slug=kb_slug,
            image_store=self._image_store,
            http_client=self._image_http,
            parsed_images=parsed_images,
        )

    async def _fail_sync_run(self, sync_run_id: uuid.UUID, error_message: str) -> None:
        """Mark a sync run as failed without a full portal config."""
        async with self._session_maker() as session:
            sync_run = await session.get(SyncRun, sync_run_id)
            if sync_run:
                sync_run.status = SyncStatus.FAILED
                sync_run.completed_at = datetime.now(UTC)
                sync_run.error_details = [{"error": error_message}]
                await session.commit()

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
