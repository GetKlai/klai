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
                status_code=500, detail="encryption_key_not_configured",
            ) from exc
        logger.exception("crawl_sync_bad_kek", connector_id=str(connector_id))
        raise HTTPException(status_code=500, detail="encryption_key_invalid") from exc


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

    include_patterns = [req.path_prefix] if req.path_prefix else None

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    await proc_app.run_crawl.defer_async(  # type: ignore[attr-defined]
        job_id=job_id,
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=req.base_url,
        max_depth=req.max_depth,
        max_pages=req.max_pages,
        include_patterns=include_patterns,
        exclude_patterns=None,
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
        start_url=req.base_url,
    )
    return CrawlSyncResponse(job_id=job_id, status="queued")


@router.get(
    "/ingest/v1/crawl/sync/{job_id}/status",
    response_model=CrawlSyncStatusResponse,
)
async def crawl_sync_status(job_id: str) -> CrawlSyncStatusResponse:
    """Return the current state of a crawl job for polling callers."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, pages_total, pages_done, error
        FROM knowledge.crawl_jobs
        WHERE id = $1
        """,
        job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    return CrawlSyncStatusResponse(
        job_id=str(row["id"]),
        status=row["status"],
        pages_total=row["pages_total"],
        pages_done=row["pages_done"],
        error=row["error"],
    )
