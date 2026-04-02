"""Client for calling knowledge-ingest internal API."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_graph_stats(org_id: str) -> dict[str, int | None]:
    """Fetch entity/edge counts from knowledge-ingest (FalkorDB graph).

    Returns {"entity_count": N, "edge_count": N} on success,
    or {"entity_count": None, "edge_count": None} on failure.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={"X-Internal-Secret": settings.knowledge_ingest_secret},
            timeout=5.0,
        ) as client:
            resp = await client.get(
                "/ingest/v1/graph-stats",
                params={"org_id": org_id},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("Could not fetch graph stats from knowledge-ingest (org=%s)", org_id)
        return {"entity_count": None, "edge_count": None}


async def get_source_count(org_id: str, kb_slug: str) -> int | None:
    """Fetch the number of active source artifacts for a KB from knowledge-ingest."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={"X-Internal-Secret": settings.knowledge_ingest_secret},
            timeout=5.0,
        ) as client:
            resp = await client.get(
                "/ingest/v1/source-count",
                params={"org_id": org_id, "kb_slug": kb_slug},
            )
            resp.raise_for_status()
            return resp.json().get("source_count")
    except Exception:
        logger.warning("Could not fetch source count from knowledge-ingest (org=%s kb=%s)", org_id, kb_slug)
        return None


async def delete_kb(org_id: str, kb_slug: str) -> None:
    """Delete all knowledge-ingest data for a KB: FalkorDB graph nodes, Qdrant chunks, PostgreSQL records.

    Intentionally raises on failure (no try/except). The portal endpoint must not delete its own
    record when ingest cleanup fails — letting the exception propagate to a 500 keeps both sides
    consistent and forces an explicit retry rather than silently orphaning data.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={"X-Internal-Secret": settings.knowledge_ingest_secret},
        timeout=30.0,
    ) as client:
        resp = await client.delete(
            "/ingest/v1/kb",
            params={"org_id": org_id, "kb_slug": kb_slug},
        )
        resp.raise_for_status()


async def preview_crawl(
    url: str, content_selector: str | None = None, org_id: str = ""
) -> dict:
    """Call knowledge-ingest preview endpoint and return fit_markdown + word_count.

    Returns {"fit_markdown": "", "word_count": 0, "url": url} on any failure so the caller
    can always render a safe empty state.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={"X-Internal-Secret": settings.knowledge_ingest_secret},
            timeout=20.0,
        ) as client:
            resp = await client.post(
                "/ingest/v1/crawl/preview",
                json={"url": url, "content_selector": content_selector, "org_id": org_id},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
    except Exception:
        logger.warning("preview_crawl failed", extra={"url": url})
        return {"fit_markdown": "", "word_count": 0, "url": url}


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
    except Exception:
        logger.exception(
            "Failed to sync KB visibility to knowledge-ingest (org=%s kb=%s visibility=%s)",
            org_id,
            kb_slug,
            visibility,
        )
