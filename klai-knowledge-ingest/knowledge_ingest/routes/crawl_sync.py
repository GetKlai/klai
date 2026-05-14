"""Bulk-sync crawl endpoint (SPEC-CRAWLER-004 Fase C).

``POST /ingest/v1/crawl/sync`` replaces the per-adapter klai-connector web
crawler flow with a single internal API. klai-connector (once Fase D lands)
sends the connector_id + config here; knowledge-ingest looks up the
encrypted cookies, decrypts them in-process via the shared
``klai-connector-credentials`` library (REQ-01.3 — plaintext cookies never
leave a service boundary), creates a ``knowledge.crawl_jobs`` row, enqueues
the Procrastinate ``run_crawl`` task, and returns ``{job_id, status}`` in
under 500 ms (REQ-03.2).

Polling: ``GET /ingest/v1/crawl/sync/{job_id}/status`` reads the row and
echoes ``status`` + ``pages_done`` + ``pages_total`` so the caller
(klai-connector's sync_engine in Fase D) can drive sync_runs state.

The endpoint sits behind the existing ``InternalSecretMiddleware`` — no
additional auth check required; unauthenticated requests never reach the
route handler.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from knowledge_ingest.config import settings
from knowledge_ingest.connector_cookies import (
    ConnectorDecryptError,
    ConnectorNotFoundError,
    ConnectorOrgMismatchError,
    load_connector_cookies,
)
from knowledge_ingest.db import get_pool

logger = structlog.get_logger()
router = APIRouter()


# @MX:ANCHOR: CrawlSyncRequest -- stable contract between klai-connector and knowledge-ingest
# @MX:REASON: Adding/renaming a field breaks the delegation path added in Fase D.
# @MX:SPEC: SPEC-CRAWLER-004 REQ-03.1
class CrawlSyncRequest(BaseModel):
    """Payload for ``POST /ingest/v1/crawl/sync``.

    Callers send ``connector_id`` rather than any secret. knowledge-ingest
    resolves the cookies itself via the shared credentials library so
    plaintext cookies never cross a service boundary.
    """

    connector_id: uuid.UUID
    org_id: str
    kb_slug: str
    base_url: str
    max_pages: int = Field(default=200, ge=1, le=10000)
    path_prefix: str | None = None
    content_selector: str | None = None
    canary_url: str | None = None
    canary_fingerprint: str | None = None
    login_indicator: str | None = None
    max_depth: int = Field(default=3, ge=1, le=10)


class CrawlSyncResponse(BaseModel):
    job_id: str
    status: str


class CrawlSyncStatusResponse(BaseModel):
    job_id: str
    status: str
    pages_total: int | None
    pages_done: int | None
    error: str | None


_CRAWL_WORKER_LOST_ERROR = "crawl_worker_lost"
_FAILED_PARTIAL_STATUS = "failed_partial"
_RUNNABLE_CRAWL_STATUSES = {"pending", "running"}
_TERMINAL_PROCRASTINATE_STATUSES = {"failed", "cancelled", "aborted", "succeeded"}
_BLOG_ARCHIVE_SEGMENTS = ("tag", "tags", "category", "categories", "author", "page")


async def _validate_connector(
    connector_id: uuid.UUID,
    org_id: str,
) -> None:
    """Validate connector exists + decryption would succeed, without keeping plaintext.

    SPEC-CRAWLER-004 fix for REQ-05.4: decrypted cookies must never be passed
    to the Procrastinate task as kwargs (the worker logs args verbatim). So
    the endpoint only verifies that a decrypt WOULD work and enqueues just
    the ``connector_id``; the task reloads the cookies at run time via the
    same helper. Plaintext cookies live only in memory, per-request.

    Raises:
        HTTPException(404): connector_id not found.
        HTTPException(409): zitadel_org_id mismatch.
        HTTPException(500): ENCRYPTION_KEY missing / malformed / decrypt fails.
    """
    pool = await get_pool()
    try:
        await load_connector_cookies(
            connector_id=connector_id,
            expected_zitadel_org_id=org_id,
            pool=pool,
            kek_hex=settings.encryption_key,
        )
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail="connector_not_found") from exc
    except ConnectorOrgMismatchError as exc:
        raise HTTPException(status_code=409, detail="connector_org_mismatch") from exc
    except ConnectorDecryptError as exc:
        logger.error(
            "crawl_sync_decrypt_failed",
            connector_id=str(connector_id),
            reason="auth_tag_mismatch",
        )
        raise HTTPException(status_code=500, detail="decrypt_failed") from exc
    except ValueError as exc:
        # Raised by load_connector_cookies when KEK missing/malformed.
        msg = str(exc)
        if "not_configured" in msg:
            raise HTTPException(
                status_code=500,
                detail="encryption_key_not_configured",
            ) from exc
        logger.exception("crawl_sync_bad_kek", connector_id=str(connector_id))
        raise HTTPException(status_code=500, detail="encryption_key_invalid") from exc


def _default_exclude_patterns(normalized_path_prefix: str | None) -> list[str] | None:
    """Exclude collection/archive pages that should not count as content."""
    if normalized_path_prefix != "/blog":
        return None
    return [f"/blog/{segment}/*" for segment in _BLOG_ARCHIVE_SEGMENTS]


@router.post(
    "/ingest/v1/crawl/sync",
    response_model=CrawlSyncResponse,
    status_code=202,
)
async def crawl_sync(req: CrawlSyncRequest) -> CrawlSyncResponse:
    """Enqueue a bulk web crawl; cookies load at task run-time, not enqueue-time."""
    # Fail fast: confirm the connector exists + cookies would decrypt. Do NOT
    # persist the plaintext — the task will reload at run time.
    await _validate_connector(req.connector_id, req.org_id)

    job_id = str(uuid.uuid4())
    now = int(time.time())
    config_for_audit = req.model_dump(mode="json")
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.crawl_jobs
            (id, org_id, kb_slug, config, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'pending', $5, $5)
        """,
        job_id,
        req.org_id,
        req.kb_slug,
        json.dumps(config_for_audit),
        now,
    )

    # crawl4ai URLPatternFilter classifies '/nl/' as exact-match (fnmatch with no
    # wildcards) so '/nl/6-bubble' never matches. Appending '/*' makes it a PREFIX
    # pattern, which is the intended semantics for a path_prefix filter.
    #
    # Portal may store path_prefix as either '/blog' or 'blog'. Normalize once
    # before composing start_url; otherwise 'https://host/' + 'blog' becomes the
    # invalid host-like URL 'https://hostblog'.
    normalized_path_prefix = (
        "/" + req.path_prefix.strip().lstrip("/")
        if req.path_prefix and req.path_prefix.strip()
        else None
    )
    include_patterns = (
        [normalized_path_prefix.rstrip("/") + "/*"]
        if normalized_path_prefix
        else None
    )
    exclude_patterns = _default_exclude_patterns(normalized_path_prefix)

    # BFS must enter the graph at a node that links into the allowed subtree.
    # If the root page only links to sibling language paths (e.g. wiki shows
    # /en/ but user asked for /nl/), starting on req.base_url makes the filter
    # reject every outgoing link and the crawl halts after 1 page. Starting
    # on base_url + path_prefix gives BFS a seeded entry inside the filter set.
    start_url = req.base_url
    if normalized_path_prefix:
        start_url = req.base_url.rstrip("/") + normalized_path_prefix

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    await proc_app.run_crawl.defer_async(  # type: ignore[attr-defined]
        job_id=job_id,
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=start_url,
        max_depth=req.max_depth,
        max_pages=req.max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        rate_limit=2.0,
        content_selector=req.content_selector,
        login_indicator_selector=req.login_indicator,
        # REQ-05.4: connector_id only — plaintext cookies never enter the
        # Procrastinate args column or the worker's "Starting job" log.
        connector_id=str(req.connector_id),
        canary_url=req.canary_url,
        canary_fingerprint=req.canary_fingerprint,
    )

    logger.info(
        "crawl_sync_enqueued",
        job_id=job_id,
        connector_id=str(req.connector_id),
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=start_url,
        exclude_patterns=exclude_patterns,
    )
    return CrawlSyncResponse(job_id=job_id, status="queued")


def _procrastinate_job_can_still_progress(proc_row: Mapping[str, Any] | None) -> bool:
    """Return whether the queued worker task can still advance its crawl_job."""
    if proc_row is None:
        # Older/manual rows may not have a matching procrastinate row. Treat
        # absence as unknown, not failed, so reads do not invent terminal state.
        return True

    proc_status = str(proc_row["status"])
    worker_id = proc_row["worker_id"]
    if proc_status in _TERMINAL_PROCRASTINATE_STATUSES:
        return False
    if proc_status == "doing" and worker_id is None:
        return False
    return True


def _crawl_sync_error(row: Mapping[str, Any]) -> str | None:
    """Return the stable error code exposed to polling callers."""
    if row.get("error"):
        return str(row["error"])
    if row.get("status") != _FAILED_PARTIAL_STATUS:
        return None

    summary = row.get("error_summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return _FAILED_PARTIAL_STATUS
    if isinstance(summary, Mapping) and summary.get("reason"):
        return str(summary["reason"])
    return _FAILED_PARTIAL_STATUS


async def _reconcile_crawl_job_lifecycle(
    pool: Any,
    row: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Fail crawl_jobs whose Procrastinate task cannot still make progress.

    A deploy restart can leave Procrastinate rows in ``doing`` after their
    worker disappeared. In that state ``knowledge.crawl_jobs`` would stay
    ``running`` forever, and klai-connector's reaper would keep trusting the
    stale remote status. The status endpoint is the read-side reconciliation
    point used by both the UI resolver and the reaper, so it is the right place
    to convert an impossible-to-progress crawl into a terminal failure.
    """
    if row["status"] not in _RUNNABLE_CRAWL_STATUSES:
        return row

    proc_row = await pool.fetchrow(
        """
        SELECT status::text AS status, worker_id
        FROM procrastinate_jobs
        WHERE task_name = 'knowledge_ingest.crawl_tasks.run_crawl'
          AND args->>'job_id' = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        row["id"],
    )
    if _procrastinate_job_can_still_progress(proc_row):
        return row

    proc_status = str(proc_row["status"])
    worker_id = proc_row["worker_id"]
    now = int(time.time())
    await pool.execute(
        """
        UPDATE knowledge.crawl_jobs
        SET status = 'failed', error = $2, updated_at = $3
        WHERE id = $1 AND status IN ('pending', 'running') AND error IS DISTINCT FROM $2
        """,
        row["id"],
        _CRAWL_WORKER_LOST_ERROR,
        now,
    )
    logger.warning(
        "crawl_sync_status_orphaned_job_failed",
        job_id=row["id"],
        proc_status=proc_status,
        worker_id=worker_id,
    )
    return {
        **dict(row),
        "status": "failed",
        "error": _CRAWL_WORKER_LOST_ERROR,
    }


@router.get(
    "/ingest/v1/crawl/sync/{job_id}/status",
    response_model=CrawlSyncStatusResponse,
)
async def crawl_sync_status(job_id: str) -> CrawlSyncStatusResponse:
    """Return the current state of a crawl job for polling callers."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, pages_total, pages_done, error, error_summary
        FROM knowledge.crawl_jobs
        WHERE id = $1
        """,
        job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = await _reconcile_crawl_job_lifecycle(pool, row)

    return CrawlSyncStatusResponse(
        job_id=str(row["id"]),
        status=row["status"],
        pages_total=row["pages_total"],
        pages_done=row["pages_done"],
        error=_crawl_sync_error(row),
    )


@router.post(
    "/ingest/v1/crawl/sync/{job_id}/cancel",
    status_code=204,
)
async def crawl_sync_cancel(job_id: str) -> None:
    """Cancel an in-flight ``run_crawl`` procrastinate task.

    SPEC-WORKER-LANES-001. klai-connector's ``sync_engine`` calls this on
    poll timeout so the procrastinate task does not keep writing artifacts
    behind a sync_run that is already marked failed. Without it, the
    user-visible state (sync_run.status='failed') diverges from the data
    state (artifacts continue to accumulate after the failure).

    Idempotent:

    * If the procrastinate task is already finished (succeeded / failed /
      aborted), this returns 204 — there is nothing to cancel and the data
      is already where it should be.
    * If the underlying ``knowledge.crawl_jobs`` row is unknown, returns 404
      so the caller can distinguish "wrong job_id" from "already done".
    """
    pool = await get_pool()
    crawl_row = await pool.fetchrow(
        "SELECT id, status FROM knowledge.crawl_jobs WHERE id = $1",
        job_id,
    )
    if crawl_row is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    # Find the matching procrastinate run_crawl task. The task args carry
    # ``job_id`` (the crawl_jobs.id, not procrastinate's own id) so we look
    # it up there. Most recent matching row wins — retried jobs would have
    # multiple, the live one is the newest in 'todo' or 'doing'.
    proc_row = await pool.fetchrow(
        """
        SELECT id, status FROM procrastinate_jobs
        WHERE task_name = 'knowledge_ingest.crawl_tasks.run_crawl'
          AND args->>'job_id' = $1
          AND status IN ('todo', 'doing')
        ORDER BY id DESC
        LIMIT 1
        """,
        job_id,
    )
    if proc_row is None:
        # No live procrastinate row — task already finished (succeeded /
        # failed / aborted) or was never enqueued. Either way, cancel is a
        # no-op; the caller's intent (stop the work) is satisfied.
        logger.info(
            "crawl_sync_cancel_noop",
            job_id=job_id,
            crawl_status=crawl_row["status"],
        )
        return

    # Lazy import to keep procrastinate optional in test environments
    # where ENRICHMENT_ENABLED=false.
    import procrastinate  # noqa: F401

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    if proc_app is None:
        # Worker bootstrap hasn't completed yet — the task can't be running
        # either, so this is effectively the no-op path.
        logger.warning(
            "crawl_sync_cancel_proc_app_unavailable",
            job_id=job_id,
            proc_job_id=proc_row["id"],
        )
        return

    try:
        # ``abort=True`` signals the running worker to interrupt the task at
        # the next safe checkpoint via the ``abort_requested`` column.
        # ``delete_job=False`` keeps the row for observability (status
        # transitions to ``cancelled``).
        await proc_app.job_manager.cancel_job_by_id_async(
            proc_row["id"], abort=True, delete_job=False
        )
        logger.info(
            "crawl_sync_cancel_requested",
            job_id=job_id,
            proc_job_id=proc_row["id"],
            previous_status=proc_row["status"],
        )
    except Exception:
        # Cancel is best-effort. If procrastinate raises (e.g. job
        # transitioned to a terminal state mid-call), we still return 204:
        # the caller's intent is satisfied because the task is no longer
        # going to write new data.
        logger.exception(
            "crawl_sync_cancel_failed",
            job_id=job_id,
            proc_job_id=proc_row["id"],
        )
