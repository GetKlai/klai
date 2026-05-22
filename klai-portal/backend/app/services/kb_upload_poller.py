"""Background poller that drives kb_uploads through to terminal states.

SPEC-KB-FILE-UPLOAD-001 — docling-serve runs document parsing in its
own async queue. Portal-api persists the ``task_id`` in ``kb_uploads``
on submission and this poller advances each row through the workflow:

::

    processing  ──── docling task complete ────▶  ingesting
                                                     │
                                                     ▼
                                          /ingest/v1/document
                                                     │
                                                     ▼
                                                  done / failed

The poller runs as a single background asyncio task started in the
FastAPI lifespan. Failure of one row never blocks the rest. State
transitions go through ``kb_uploads_repo`` so the cat-D RLS WITH-CHECK
clause matches (every UPDATE runs inside a tenant-scoped session).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import structlog
from sqlalchemy import select

from app.core.database import cross_org_session, tenant_scoped_session
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.services import (
    docling_client,
    file_upload,
    kb_uploads_repo,
    knowledge_ingest_client,
)
from app.services.kb_uploads_repo import KBUploadView

logger = structlog.get_logger()

# How often the poller wakes up. Docling parsing for a 100 MB PDF
# takes 30 s+ on CPU, so a 5 s tick is plenty granular.
DEFAULT_POLL_INTERVAL_S: float = 5.0

# Page size per tick. Caps the work we attempt under load; rows we
# skip on this tick are picked up on the next.
_BATCH_SIZE: int = 50


# ---- Per-row processing ---------------------------------------------------


async def _process_processing_row(view: KBUploadView) -> None:
    """Advance a row whose docling task is still running.

    Polls docling-serve for the current status. On success, fetches
    the markdown body, forwards it to ``/ingest/v1/document`` and
    transitions the row to ``done``. On failure (or any non-recoverable
    error), transitions to ``failed`` with a structured reason.

    Transient errors (timeouts, connection blips) leave the row at
    ``processing`` so the next tick retries.
    """
    if not view.docling_task_id:
        # Defensive: a processing row should always have a task_id.
        # Mark it failed so the operator can investigate.
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="missing_docling_task")
            await db.commit()
        return

    try:
        poll = await docling_client.poll_status(view.docling_task_id)
    except docling_client.DoclingTimeoutError:
        # Transient — leave for next tick. Bump updated_at so the
        # poller sorts this row to the back of the queue and works on
        # other rows first.
        logger.info(
            "kb_upload_poll_transient",
            upload_id=str(view.id),
            task_id=view.docling_task_id,
        )
        return
    except docling_client.DoclingError:
        logger.exception("kb_upload_poll_docling_error", upload_id=str(view.id))
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="docling_unreachable")
            await db.commit()
        return

    if not poll.terminal:
        return

    if poll.status != docling_client.DoclingTaskStatus.SUCCESS:
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(
                db,
                upload_id=view.id,
                failure_reason="extraction_failed",
            )
            await db.commit()
        logger.warning(
            "kb_upload_docling_failed",
            upload_id=str(view.id),
            docling_status=poll.status,
            error_message=poll.error_message,
        )
        return

    # docling reports success — fetch the chunks/result and advance to ingesting.
    try:
        result = await docling_client.get_result_document(view.docling_task_id)
    except docling_client.DoclingError:
        logger.exception(
            "kb_upload_result_fetch_failed",
            upload_id=str(view.id),
            task_id=view.docling_task_id,
        )
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="extraction_failed")
            await db.commit()
        return

    async with tenant_scoped_session(view.org_id) as db:
        await kb_uploads_repo.mark_ingesting(db, upload_id=view.id)
        await db.commit()

    await _ingest_and_finish(view, result=result)


async def _process_ingesting_row(view: KBUploadView) -> None:
    """Retry the ingest call for a row stuck in ``ingesting`` state.

    Reaches this branch when the previous tick's ``mark_ingesting`` +
    ``forward_ingest`` was interrupted (portal-api restart, network
    blip). Re-fetch the docling result and try again — idempotent
    because knowledge-ingest dedupes on ``source_ref``.
    """
    if not view.docling_task_id:
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="missing_docling_task")
            await db.commit()
        return

    try:
        result = await docling_client.get_result_document(view.docling_task_id)
    except docling_client.DoclingResultNotFoundError:
        logger.exception(
            "kb_upload_ingesting_result_not_found",
            upload_id=str(view.id),
        )
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="docling_result_not_found")
            await db.commit()
        return
    except docling_client.DoclingError:
        logger.exception(
            "kb_upload_ingesting_result_fetch_failed",
            upload_id=str(view.id),
        )
        # Stay in ingesting; next tick retries. If docling restarted
        # and lost the task, the result fetch will keep failing — an
        # operator alert (Grafana log query) catches the long-stuck row.
        return

    await _ingest_and_finish(view, result=result)


async def _ingest_and_finish(view: KBUploadView, *, result: docling_client.DoclingIngestResult) -> None:
    """Forward a docling-derived document result to knowledge-ingest.

    Resolves the KB + Org for the row, builds the IngestRequest payload
    in the same shape ``app_knowledge_sources._forward_ingest`` uses,
    and transitions ``kb_uploads`` to ``done`` with the resulting
    artifact_id.
    """
    async with tenant_scoped_session(view.org_id) as db:
        kb_result = await db.execute(select(PortalKnowledgeBase).where(PortalKnowledgeBase.id == view.kb_id))
        kb = kb_result.scalar_one_or_none()
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == view.org_id))
        org = org_result.scalar_one_or_none()

        if kb is None or org is None:
            kb_slug = None
            kb_name = None
            org_zitadel_id = None
            kb_owner_type = None
        else:
            # Copy ORM-backed values while the session is still open. The
            # session context commits on exit, which expires attributes; using
            # ORM instances afterwards can raise DetachedInstanceError.
            kb_slug = kb.slug
            kb_name = kb.name
            org_zitadel_id = org.zitadel_org_id
            kb_owner_type = kb.owner_type

    if kb_slug is None or kb_name is None or org_zitadel_id is None:
        logger.error(
            "kb_upload_ingest_kb_or_org_missing",
            upload_id=str(view.id),
            kb_id=view.kb_id,
            org_id=view.org_id,
        )
        async with tenant_scoped_session(view.org_id) as db:
            await kb_uploads_repo.mark_failed(db, upload_id=view.id, failure_reason="kb_or_org_missing")
            await db.commit()
        return

    title = file_upload.derive_title(view.filename, view.extension)
    source_content_hash = view.source_ref.removeprefix("file:sha256:")
    payload: dict[str, object] = {
        "org_id": org_zitadel_id,
        "kb_slug": kb_slug,
        "path": view.source_ref,
        "content": result.content,
        "content_hash": source_content_hash,
        "title": title,
        "source_type": "file",
        "content_type": "document",
        "source_ref": view.source_ref,
        "kb_name": kb_name,
        "extra": {
            "original_filename": view.filename,
            "extension": view.extension,
            "mime": view.mime,
            "bytes": view.bytes,
            "pipeline": "docling",
            "docling_task_id": view.docling_task_id,
            "docling_chunk_count": result.chunk_count,
            "document_text_truncated": result.chunks is not None,
        },
    }
    if result.chunks is not None:
        payload["skip_chunking"] = True
        payload["chunks"] = list(result.chunks)

    # Personal KBs: knowledge-ingest verifies the caller owns the personal KB
    # and rejects with `personal_kb_owner_mismatch` (HTTP 403) when no
    # claimed_user_id is supplied. The request-path ingest (_forward_ingest in
    # app_knowledge_sources) passes user_id for owner_type=="user"; this async
    # poller must do the same or every personal-KB PDF/docling upload hangs at
    # `ingesting` forever. The uploader's id is persisted on the row.
    if kb_owner_type == "user":
        payload["user_id"] = view.created_by

    try:
        artifact_id = await knowledge_ingest_client.ingest_document(payload)
    except (httpx.HTTPStatusError, httpx.RequestError):
        logger.exception(
            "kb_upload_ingest_forward_failed",
            upload_id=str(view.id),
            kb_slug=kb_slug,
        )
        # Stay at ingesting — knowledge-ingest may be transiently down.
        # Operator alert catches rows stuck > N minutes.
        return

    async with tenant_scoped_session(view.org_id) as db:
        await kb_uploads_repo.mark_done(db, upload_id=view.id, artifact_id=artifact_id)
        await db.commit()
    logger.info(
        "kb_upload_done",
        upload_id=str(view.id),
        artifact_id=artifact_id,
        kb_slug=kb_slug,
        bytes=view.bytes,
    )


# ---- Loop -----------------------------------------------------------------


async def _process_one_view(view: KBUploadView) -> None:
    """Dispatch a single row to the correct phase handler."""
    try:
        if view.status == kb_uploads_repo.STATUS_PROCESSING:
            await _process_processing_row(view)
        elif view.status == kb_uploads_repo.STATUS_INGESTING:
            await _process_ingesting_row(view)
        # Terminal statuses are filtered out by list_pending; if one
        # slipped through it is a no-op.
    except Exception:
        # Never let one row's failure abort the loop. exc_info gives
        # operators the trace via VictoriaLogs.
        logger.exception("kb_upload_poll_row_unexpected", upload_id=str(view.id))


async def poll_once() -> int:
    """Run a single poll pass. Returns the number of rows processed.

    Exposed for tests + manual recovery.
    """
    async with cross_org_session() as db:
        pending = await kb_uploads_repo.list_pending(db, limit=_BATCH_SIZE)

    for view in pending:
        await _process_one_view(view)

    return len(pending)


async def run_poll_loop(
    *,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
    stop_event: asyncio.Event | None = None,
    on_tick: Callable[[int], Awaitable[None]] | None = None,
) -> None:
    """Background loop — call once from the FastAPI lifespan.

    The loop terminates when ``stop_event`` is set. ``on_tick`` is
    invoked after each pass with the number of rows processed; tests
    use it to drive deterministic single-tick assertions.
    """
    logger.info("kb_upload_poller_started", interval_s=interval_s)
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            count = await poll_once()
        except Exception:
            logger.exception("kb_upload_poller_tick_unexpected")
            count = 0

        if on_tick is not None:
            try:
                await on_tick(count)
            except Exception:
                logger.exception("kb_upload_poller_on_tick_unexpected")

        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            break

    logger.info("kb_upload_poller_stopped")
