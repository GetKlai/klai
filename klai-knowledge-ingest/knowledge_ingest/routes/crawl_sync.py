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
from typing import Any

import structlog
from connector_credentials import ConnectorCredentialStore
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from knowledge_ingest.config import settings
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


async def _load_connector_cookies(
    connector_id: uuid.UUID,
    org_id: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Fetch cookies for a connector via the shared credentials library.

    Raises:
        HTTPException(404): connector_id does not exist.
        HTTPException(409): org_id mismatch between request and connector row
            (guards against lateral access via a known connector id).
        HTTPException(500): ENCRYPTION_KEY invalid / decryption failed.

    Returns:
        Tuple ``(connector_org_id, cookies)``. An empty list is returned when
        the connector has no encrypted_credentials (public-web crawl).
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT c.id, c.org_id, c.encrypted_credentials, o.connector_dek_enc
        FROM portal_connectors c
        JOIN portal_orgs o ON o.id = c.org_id
        WHERE c.id = $1
        """,
        connector_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="connector_not_found")

    connector_org_id = int(row["org_id"])
    if str(connector_org_id) != str(org_id):
        raise HTTPException(status_code=409, detail="connector_org_mismatch")

    encrypted = row["encrypted_credentials"]
    dek_enc = row["connector_dek_enc"]
    if not encrypted or not dek_enc:
        # Public-web crawl; no cookies needed.
        return connector_org_id, []

    if not settings.encryption_key:
        raise HTTPException(status_code=500, detail="encryption_key_not_configured")

    try:
        store = ConnectorCredentialStore(settings.encryption_key)
        payload = store.decrypt_credentials_from_blobs(
            encrypted_credentials=bytes(encrypted),
            connector_dek_enc=bytes(dek_enc),
        )
    except ValueError as exc:  # bad KEK format
        logger.exception("crawl_sync_bad_kek", connector_id=str(connector_id))
        raise HTTPException(status_code=500, detail="encryption_key_invalid") from exc
    except InvalidTag as exc:
        logger.error(
            "crawl_sync_decrypt_failed",
            connector_id=str(connector_id),
            reason="auth_tag_mismatch",
        )
        raise HTTPException(status_code=500, detail="decrypt_failed") from exc

    cookies = payload.get("cookies") or []
    return connector_org_id, cookies


@router.post(
    "/ingest/v1/crawl/sync",
    response_model=CrawlSyncResponse,
    status_code=202,
)
async def crawl_sync(req: CrawlSyncRequest) -> CrawlSyncResponse:
    """Enqueue a bulk web crawl with cookies loaded from portal_connectors."""
    _org_id_int, cookies = await _load_connector_cookies(req.connector_id, req.org_id)

    job_id = str(uuid.uuid4())
    now = int(time.time())
    config_for_audit = req.model_dump(mode="json")
    # Never persist cookies into knowledge.crawl_jobs.config — the audit row is
    # meant for operators and plaintext cookies have no business being there.
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
        cookies=cookies,
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
        cookie_count=len(cookies),
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
