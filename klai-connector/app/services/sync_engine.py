"""Sync orchestrator with global semaphore and per-connector locking."""

import asyncio
import base64
import time
import uuid
from datetime import UTC, datetime
from typing import Any

# MIME → filename extension map for Unstructured parser output. Kept
# local to the connector because the shared lib intentionally does not
# know the parser's envelope format.
_PARSER_MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def _ext_from_parser_mime(mime: str) -> str:
    """Return the extension for a parser-provided MIME type, default ``png``."""
    return _PARSER_MIME_EXT.get(mime, "png")

import httpx
from gidgethub import BadRequest
from klai_image_storage import (
    ImageStore,
    ParsedImage,
    PinnedResolverTransport,
    download_and_upload_adapter_images,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import DocumentRef
from app.adapters.oauth_base import OAuthReconnectRequiredError
from app.adapters.registry import AdapterRegistry
from app.clients.knowledge_ingest import CrawlSyncClient, KnowledgeIngestClient
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.services.parser import parse_document_with_images
from app.services.portal_client import PortalClient
from app.services.url_guard import PersistedUrlRejectedError

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

    # SPEC-CRAWLER-004 Fase D — poll cadence + total timeout for the
    # delegation path. 5 s matches the SPEC; 30 min matches klai-connector's
    # historical webcrawler timeout so behaviour stays unchanged for ops.
    _WEB_CRAWLER_POLL_INTERVAL_S: float = 5.0
    _WEB_CRAWLER_POLL_TIMEOUT_S: float = 30 * 60

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        registry: AdapterRegistry,
        ingest_client: KnowledgeIngestClient,
        portal_client: PortalClient,
        image_store: ImageStore | None = None,
        crawl_sync_client: CrawlSyncClient | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._registry = registry
        self._ingest_client = ingest_client
        self._portal_client = portal_client
        self._image_store = image_store
        # SPEC-SEC-SSRF-001 REQ-7.4 / REQ-7.6 / AC-23: wrap the image
        # http client in a ``PinnedResolverTransport`` so every adapter
        # image fetch (Notion / Confluence / GitHub / Airtable) inherits
        # DNS-rebinding defence without per-adapter boilerplate. The
        # guard call in klai_image_storage.pipeline runs first and
        # populates the transport's pin map via ``_image_transport``
        # below. Unpinned hosts still fall through to normal DNS, so
        # tests that don't seed pins keep working.
        self._image_transport: PinnedResolverTransport | None
        if image_store:
            self._image_transport = PinnedResolverTransport()
            self._image_http = httpx.AsyncClient(
                transport=self._image_transport, timeout=30.0,
            )
        else:
            self._image_transport = None
            self._image_http = None
        self._crawl_sync_client = crawl_sync_client
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

        # SPEC-CRAWLER-004 Fase D: delegate web_crawler syncs to
        # knowledge-ingest. klai-connector no longer runs crawl4ai itself;
        # instead it POSTs the config to /ingest/v1/crawl/sync and polls
        # the returned job_id. Keeps sync_runs ownership + product_events
        # on the connector side; moves pipeline execution to ingest.
        if (
            portal_config.connector_type == "web_crawler"
            and self._crawl_sync_client is not None
        ):
            await self._run_web_crawler_delegation(
                portal_config=portal_config,
                connector_id=connector_id,
                sync_run_id=sync_run_id,
                start_time=start_time,
            )
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
                sync_run.error_details = [{"error": f"Unsupported connector type: {portal_config.connector_type!r}"}]
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
            refs: list[DocumentRef] = []  # all discovered refs (for cursor_state synced_refs)

            try:
                cursor_state = await adapter.get_cursor_state(portal_config)

                last_run = await self._get_last_successful_run(session, connector_id)
                last_pending = await self._get_last_pending_run(session, connector_id)

                # Resume state: refs already ingested in an interrupted run.
                resume_ingested_refs: set[str] = set()
                if last_pending and last_pending.cursor_state:
                    resume_ingested_refs = set(last_pending.cursor_state.get("ingested_refs", []))
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
                        connector_id,
                        len(refs_to_sync),
                        documents_skipped,
                        len(refs),
                    )

                for ref in refs_to_sync:
                    ref_key = ref.source_ref or ref.path

                    try:
                        content_bytes = await adapter.fetch_document(ref, portal_config)
                        bytes_processed += len(content_bytes)
                        parse_result = parse_document_with_images(
                            content_bytes,
                            ref.path.split("/")[-1],
                        )
                        text = parse_result.text
                        if len(text.strip()) < 50:
                            logger.info(
                                "Skipping short document (path=%s, chars=%d)",
                                ref.path,
                                len(text.strip()),
                            )
                            documents_ok += 1
                            resume_ingested_refs.add(ref_key)
                            continue

                        # Image upload (when Garage is configured). Each adapter
                        # populates ref.images with absolute URLs; we just upload them.
                        image_urls: list[str] | None = None
                        if self._image_store and self._image_http:
                            image_urls = (
                                await self._upload_images(
                                    parsed_images=parse_result.images,
                                    ref=ref,
                                    org_id=portal_config.zitadel_org_id,
                                    kb_slug=portal_config.kb_slug,
                                )
                                or None
                            )  # Convert empty list to None

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
                            connector_type=portal_config.connector_type,
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

                # @MX:NOTE: Layer C boilerplate detection and CanaryMismatchError /
                #   CrawlJobPendingError handling were removed in SPEC-CRAWLER-004
                #   Fase F. Those paths only fired for web_crawler syncs handled by
                #   the now-deleted WebCrawlerAdapter. Every web_crawler sync goes
                #   through _run_web_crawler_delegation (Fase D) which enqueues the
                #   crawl in knowledge-ingest and polls /status; failure surfaces as
                #   sync_run.status=FAILED via the remote response, and quality
                #   analysis (if ever desired) happens in knowledge-ingest.

            except OAuthReconnectRequiredError as err:
                # Provider signalled the stored credential is permanently invalid
                # (Microsoft invalid_grant after password change / consent
                # revoke / post-grace rotation; Google equivalent). The only
                # recovery is user-driven re-consent via the OAuth authorize
                # flow. Mark AUTH_ERROR so the portal surfaces a
                # "Reconnect" affordance; warning not error because the
                # cause is user-state, not our bug.
                status = SyncStatus.AUTH_ERROR
                error_details.append({"error": str(err), "reason": "reconnect_required"})
                # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                logger.warning(
                    "OAuth reconnect required for connector %s",
                    connector_id,
                    extra={"connector_id": str(connector_id)},
                )

            except BadRequest as err:
                # gidgethub raises BadRequest for 401/403; treat as auth failure
                status = SyncStatus.AUTH_ERROR if err.status_code in (401, 403) else SyncStatus.FAILED
                error_details.append({"error": str(err)})
                logger.exception(
                    "Sync failed for connector %s",
                    connector_id,
                    extra={"connector_id": str(connector_id)},
                )

            except PersistedUrlRejectedError as err:
                # SPEC-SEC-SSRF-001 REQ-8.4 / AC-21: legacy connector
                # row stored an SSRF-unsafe URL. Mark the sync failed
                # with the stable error code so ops dashboards and
                # regression tests can query it. No Atlassian SDK
                # client or HTTP request was issued — the guard fired
                # inside ``_extract_config``.
                status = SyncStatus.FAILED
                error_details.append({
                    "error": err.error_code,
                    "hostname": err.hostname or "",
                    "reason": str(err),
                })
                logger.warning(
                    "Sync blocked for connector %s: persisted URL failed SSRF guard",
                    connector_id,
                    extra={
                        "connector_id": str(connector_id),
                        "error_code": err.error_code,
                        "hostname": err.hostname,
                    },
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
            # SPEC-CRAWL-003 REQ-2: set quality_status on every completed/failed run.
            # Layer C (degraded) will override this after cluster analysis in a later step.
            if sync_run.quality_status is None:
                sync_run.quality_status = "healthy" if status == SyncStatus.COMPLETED else None
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
                    (r.source_ref or r.path) for r in refs if (r.source_ref or r.path) not in failed_refs
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

    async def _run_web_crawler_delegation(
        self,
        *,
        portal_config: Any,
        connector_id: uuid.UUID,
        sync_run_id: uuid.UUID,
        start_time: float,
    ) -> None:
        """SPEC-CRAWLER-004 Fase D delegation path.

        klai-connector keeps owning ``sync_runs`` state + product_events but
        forwards the actual crawl work to knowledge-ingest's
        ``/ingest/v1/crawl/sync`` endpoint. We store the returned ``job_id``
        on ``sync_run.cursor_state.remote_job_id`` immediately so a crash
        leaves a traceable row, then poll the remote ``/status`` every 5 s
        up to 30 min. On completion we close the sync_run with the remote
        counts; on timeout or HTTP error we close it as FAILED with
        ``error.details.service = 'knowledge-ingest'`` (REQ-03.5).
        """
        assert self._crawl_sync_client is not None

        status: str = SyncStatus.COMPLETED
        documents_total = 0
        documents_ok = 0
        documents_failed = 0
        error_details: list[dict[str, object]] = []
        remote_job_id: str | None = None

        async with self._session_maker() as session:
            sync_run = await session.get(SyncRun, sync_run_id)
            if sync_run is None:
                logger.error("SyncRun not found: %s", sync_run_id)
                return

            try:
                # REQ-03.1: submit the config (connector_id only — no cookies).
                enqueue_resp = await self._crawl_sync_client.crawl_sync(
                    connector_id=str(connector_id),
                    org_id=portal_config.zitadel_org_id,
                    kb_slug=portal_config.kb_slug,
                    config=portal_config.config,
                )
                remote_job_id = enqueue_resp["job_id"]
                sync_run.cursor_state = {
                    "remote_job_id": remote_job_id,
                    "remote_status": enqueue_resp.get("status", "queued"),
                }
                await session.commit()
                logger.info(
                    "web_crawler_delegated",
                    extra={
                        "connector_id": str(connector_id),
                        "sync_run_id": str(sync_run_id),
                        "remote_job_id": remote_job_id,
                    },
                )

                # REQ-03.4 + AC-03.4: poll until the remote job terminates
                # or we hit the timeout.
                poll_interval = self._WEB_CRAWLER_POLL_INTERVAL_S
                poll_timeout = self._WEB_CRAWLER_POLL_TIMEOUT_S
                elapsed = 0.0
                final_state: dict = {}
                while elapsed < poll_timeout:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    try:
                        poll = await self._crawl_sync_client.crawl_sync_status(remote_job_id)
                    except httpx.HTTPError as poll_err:
                        logger.warning(
                            "web_crawler_poll_failed",
                            extra={
                                "connector_id": str(connector_id),
                                "remote_job_id": remote_job_id,
                                "error": str(poll_err),
                            },
                        )
                        continue
                    remote_status = poll.get("status")
                    if remote_status in ("completed", "failed"):
                        final_state = poll
                        break

                if not final_state:
                    # Timeout without a terminal state — SPEC-CRAWLER-004 EC-1.
                    # Preserve remote_job_id so a later retry can resume polling.
                    status = SyncStatus.FAILED
                    error_details = [
                        {
                            "error": "web_crawler_poll_timeout",
                            "service": "knowledge-ingest",
                            "remote_job_id": remote_job_id,
                            "timeout_seconds": int(poll_timeout),
                        },
                    ]
                else:
                    documents_total = int(final_state.get("pages_total") or 0)
                    documents_ok = int(final_state.get("pages_done") or 0)
                    if final_state.get("status") == "completed":
                        status = SyncStatus.COMPLETED
                    else:
                        status = SyncStatus.FAILED
                        documents_failed = max(0, documents_total - documents_ok)
                        remote_error = final_state.get("error") or "unknown"
                        error_details = [
                            {
                                "error": str(remote_error),
                                "service": "knowledge-ingest",
                                "remote_job_id": remote_job_id,
                            },
                        ]

            except httpx.HTTPStatusError as enqueue_err:
                # REQ-03.5: non-2xx from /crawl/sync → single failed row, no retry.
                status = SyncStatus.FAILED
                error_details = [
                    {
                        "error": f"http_{enqueue_err.response.status_code}",
                        "service": "knowledge-ingest",
                        "detail": enqueue_err.response.text[:500],
                    },
                ]
                logger.error(
                    "web_crawler_enqueue_failed",
                    extra={
                        "connector_id": str(connector_id),
                        "status_code": enqueue_err.response.status_code,
                    },
                )
            except httpx.HTTPError as enqueue_err:
                status = SyncStatus.FAILED
                error_details = [
                    {
                        "error": str(enqueue_err),
                        "service": "knowledge-ingest",
                    },
                ]
                logger.exception(
                    "web_crawler_enqueue_network_error",
                    extra={"connector_id": str(connector_id)},
                )

            duration = time.monotonic() - start_time
            completed_at = datetime.now(UTC)
            sync_run.status = status
            sync_run.completed_at = completed_at
            sync_run.quality_status = "healthy" if status == SyncStatus.COMPLETED else None
            sync_run.documents_total = documents_total
            sync_run.documents_ok = documents_ok
            sync_run.documents_failed = documents_failed
            sync_run.error_details = error_details if error_details else None
            # Keep the remote_job_id so operators can correlate a failed run
            # with the knowledge.crawl_jobs row.
            if remote_job_id is not None:
                sync_run.cursor_state = {
                    "remote_job_id": remote_job_id,
                    "remote_status": (
                        "completed" if status == SyncStatus.COMPLETED else "failed"
                    ),
                }
            await session.commit()

            logger.info(
                "web_crawler_delegation_complete",
                extra={
                    "event": "sync_complete",
                    "connector_id": str(connector_id),
                    "duration_seconds": round(duration, 1),
                    "documents_total": documents_total,
                    "documents_ok": documents_ok,
                    "documents_failed": documents_failed,
                    "status": status,
                    "remote_job_id": remote_job_id,
                },
            )

        # Portal callback (best-effort — errors swallowed in portal_client).
        await self._portal_client.report_sync_status(
            connector_id=connector_id,
            sync_run_id=sync_run_id,
            sync_status=status,
            completed_at=completed_at,
            documents_total=documents_total,
            documents_ok=documents_ok,
            documents_failed=documents_failed,
            bytes_processed=0,
            error_details=error_details if error_details else None,
        )

    async def _upload_images(
        self,
        *,
        parsed_images: list[dict[str, str]],
        ref: DocumentRef,
        org_id: str,
        kb_slug: str,
    ) -> list[str]:
        """Upload adapter-provided and parser-embedded images to S3.

        Each adapter is responsible for populating ``ref.images`` with
        resolved absolute URLs during list_documents()/fetch_document().
        The sync engine is connector-agnostic: it only iterates ref.images.

        Parser-extracted images (Unstructured PDF/DOCX output) arrive
        here as base64-encoded dicts with ``data_b64`` / ``mime_type``
        keys. We decode them locally and hand :class:`ParsedImage`
        instances to the shared lib so the lib stays unaware of the
        Unstructured envelope.
        """
        assert self._image_store is not None
        assert self._image_http is not None

        image_urls: list[tuple[str, str]] = []
        if ref.images:
            image_urls = [(img.alt, img.url) for img in ref.images]

        decoded_parsed = [
            ParsedImage(
                data=base64.b64decode(img["data_b64"]),
                ext=_ext_from_parser_mime(img.get("mime_type", "image/png")),
                source_id=ref.path,
            )
            for img in parsed_images
            if "data_b64" in img
        ]

        return await download_and_upload_adapter_images(
            image_urls=image_urls,
            org_id=org_id,
            kb_slug=kb_slug,
            image_store=self._image_store,
            http_client=self._image_http,
            parsed_images=decoded_parsed,
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
    async def _get_last_successful_run(session: AsyncSession, connector_id: uuid.UUID) -> SyncRun | None:
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
    async def _get_last_pending_run(session: AsyncSession, connector_id: uuid.UUID) -> SyncRun | None:
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
