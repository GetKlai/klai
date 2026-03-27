"""Client for calling knowledge-ingest internal API."""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def update_kb_visibility(org_id: str, kb_slug: str, visibility: str) -> None:
    """Persist KB visibility to knowledge-ingest (kb_config table + Qdrant backfill).

    Fires-and-logs-on-failure: a visibility sync error must never block the portal
    response. The Qdrant backfill is idempotent — a retry or manual re-call is safe.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={"X-Internal-Secret": settings.knowledge_ingest_secret},
            timeout=10.0,
        ) as client:
            resp = await client.patch(
                "/ingest/v1/kb/visibility",
                json={"org_id": org_id, "kb_slug": kb_slug, "visibility": visibility},
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.error(
            "Failed to sync KB visibility to knowledge-ingest (org=%s kb=%s visibility=%s): %s",
            org_id,
            kb_slug,
            visibility,
            exc,
        )
