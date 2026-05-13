"""Client for calling knowledge-ingest internal API.

SPEC-TI-003 AC-7: every call sends X-Caller-Service: portal-api so
knowledge-ingest identity-assertion can verify the caller.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)


async def get_graph_stats(org_id: str) -> dict[str, int | None]:
    """Fetch entity/edge counts from knowledge-ingest (FalkorDB graph).

    Returns {"entity_count": N, "edge_count": N} on success,
    or {"entity_count": None, "edge_count": None} on failure.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=5.0,
        ) as client:
            resp = await client.get(
                "/ingest/v1/graph-stats",
                params={"org_id": org_id},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("Could not fetch graph stats from knowledge-ingest (org=%s)", org_id, exc_info=True)
        return {"entity_count": None, "edge_count": None}


async def get_source_count(org_id: str, kb_slug: str) -> int | None:
    """Fetch the number of active source artifacts for a KB from knowledge-ingest."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=5.0,
        ) as client:
            resp = await client.get(
                "/ingest/v1/source-count",
                params={"org_id": org_id, "kb_slug": kb_slug},
            )
            resp.raise_for_status()
            return resp.json().get("source_count")
    except Exception:
        logger.warning(
            "Could not fetch source count from knowledge-ingest (org=%s kb=%s)", org_id, kb_slug, exc_info=True
        )
        return None


async def delete_kb(org_id: str, kb_slug: str) -> None:
    """Delete all knowledge-ingest data for a KB: FalkorDB graph nodes, Qdrant chunks, PostgreSQL records.

    Intentionally raises on failure (no try/except). The portal endpoint must not delete its own
    record when ingest cleanup fails — letting the exception propagate to a 500 keeps both sides
    consistent and forces an explicit retry rather than silently orphaning data.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=30.0,
    ) as client:
        resp = await client.delete(
            "/ingest/v1/kb",
            params={"org_id": org_id, "kb_slug": kb_slug},
        )
        resp.raise_for_status()


async def delete_connector(org_id: str, kb_slug: str, connector_id: str) -> None:
    """Synchronous connector cleanup — kept for admin force-purge (REQ-11).

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 keeps the synchronous DELETE for
    operator-driven recovery. The user-facing flow uses ``enqueue_purge``
    below which returns 202 immediately.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.delete(
            "/ingest/v1/connector",
            params={"org_id": org_id, "kb_slug": kb_slug, "connector_id": connector_id},
        )
        resp.raise_for_status()


async def enqueue_connector_purge(org_id: str, kb_slug: str, connector_id: str) -> None:
    """Enqueue an async connector-purge task on knowledge-ingest.

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-03. Replaces the synchronous
    ``delete_connector`` call in the user-facing DELETE endpoint. Returns
    once knowledge-ingest has handed the work to procrastinate (P95 < 50ms
    in practice). The procrastinate worker drives the cancel-jobs +
    multi-store cleanup, then calls back to the portal's
    ``finalize-delete`` endpoint to hard-delete the row.

    Raises on transport / auth failure so the caller can rollback the
    ``state='deleting'`` flip and surface a 5xx to the user.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=10.0,
    ) as client:
        resp = await client.post(
            "/ingest/v1/connector/purge",
            params={"org_id": org_id, "kb_slug": kb_slug, "connector_id": connector_id},
        )
        resp.raise_for_status()


async def preview_crawl(
    url: str,
    content_selector: str | None = None,
    org_id: str = "",
    try_ai: bool = False,
    cookies: list[dict] | None = None,
) -> dict:
    """Call knowledge-ingest preview endpoint and return fit_markdown + word_count.

    Returns {"fit_markdown": "", "word_count": 0, "url": url} on any failure so the caller
    can always render a safe empty state.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=20.0,
        ) as client:
            payload: dict = {
                "url": url,
                "content_selector": content_selector,
                "org_id": org_id,
                "try_ai": try_ai,
            }
            if cookies:
                payload["cookies"] = cookies
            resp = await client.post("/ingest/v1/crawl/preview", json=payload)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
    except Exception:
        logger.warning("preview_crawl failed", extra={"url": url}, exc_info=True)
        return {
            "fit_markdown": "",
            "word_count": 0,
            "url": url,
            "classification": "unknown",
            "classification_reason": "Preview service did not respond. Try again.",
        }


async def auth_probe(
    url: str,
    org_id: str = "",
    cookies: list[dict] | None = None,
) -> dict:
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 — call knowledge-ingest
    auth-probe endpoint.

    Returns the five-way classification + match_reasons + auth_guard. Falls
    back to ``auth_failed_unreachable`` on transport error so the wizard
    UI always has a stable shape to render.
    """
    payload: dict = {"url": url, "org_id": org_id}
    if cookies:
        payload["cookies"] = cookies
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=35.0,  # PC-1 budget: REQ-2 p95 < 30s
        ) as client:
            resp = await client.post("/ingest/v1/crawl/auth-probe", json=payload)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
    except Exception:
        logger.warning("auth_probe failed", extra={"url": url}, exc_info=True)
        return {
            "classification": "auth_failed_unreachable",
            "match_reasons": [],
            "word_count": 0,
            "auth_guard": None,
        }


async def trigger_taxonomy_bootstrap(org_id: str, kb_slug: str) -> dict:
    """Trigger bootstrap proposal generation for a KB.

    Calls knowledge-ingest to scan existing chunks and generate taxonomy
    category proposals. Returns {"documents_scanned": N, "proposals_submitted": N}.
    Raises on failure so the portal endpoint returns a clear error.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post(
            "/ingest/v1/taxonomy/bootstrap-proposals",
            json={"org_id": org_id, "kb_slug": kb_slug},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def trigger_taxonomy_backfill(org_id: str, kb_slug: str) -> dict:
    """Trigger taxonomy backfill to tag all existing chunks.

    Enqueues a Procrastinate background job in knowledge-ingest.
    Returns {"job_id": N, "status": "queued"}.
    Raises on failure so the portal endpoint returns a clear error.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=15.0,
    ) as client:
        resp = await client.post(
            "/ingest/v1/taxonomy/backfill",
            json={"org_id": org_id, "kb_slug": kb_slug},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def get_taxonomy_backfill_status(job_id: int) -> dict:
    """Proxy the backfill job status from knowledge-ingest.

    Returns {"job_id": N, "status": "queued"|"doing"|"succeeded"|"failed"}.
    Raises on failure.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=10.0,
    ) as client:
        resp = await client.get(f"/ingest/v1/taxonomy/backfill/{job_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def enqueue_auto_categorise(
    org_id: str,
    kb_slug: str,
    node_id: int,
    cluster_centroid: list[float] | None,
) -> None:
    """Enqueue an auto-categorise job in knowledge-ingest via Procrastinate.

    Best-effort: logs warning on failure but never raises.
    Replaces the old fire-and-forget asyncio.create_task pattern (SPEC-KB-026 R5).
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.post(
                "/ingest/v1/taxonomy/auto-categorise-job",
                json={
                    "org_id": org_id,
                    "kb_slug": kb_slug,
                    "node_id": node_id,
                    "cluster_centroid": cluster_centroid,
                },
            )
            resp.raise_for_status()
    except Exception:
        logger.exception(
            "enqueue_auto_categorise_failed",
            extra={"org_id": org_id, "kb_slug": kb_slug, "node_id": node_id},
        )


async def classify_gap_taxonomy(org_id: str, kb_slug: str, text: str) -> list[int]:
    """Classify a gap query against a KB's taxonomy via knowledge-ingest.

    Calls POST /ingest/v1/taxonomy/classify. Returns list of taxonomy node IDs.
    Best-effort: returns empty list on any error (timeout, connection, HTTP error).
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.post(
                "/ingest/v1/taxonomy/classify",
                json={"org_id": org_id, "kb_slug": kb_slug, "text": text},
            )
            resp.raise_for_status()
            return resp.json().get("taxonomy_node_ids", [])  # type: ignore[no-any-return]
    except Exception:
        logger.warning(
            "classify_gap_taxonomy_failed",
            extra={"org_id": org_id, "kb_slug": kb_slug},
            exc_info=True,
        )
        return []


async def ingest_document(payload: dict) -> str:
    """Forward a pre-extracted document to knowledge-ingest.

    SPEC-KB-SOURCES-001 sink for URL / YouTube / Text sources. Caller is
    responsible for supplying every required IngestRequest field
    (see ``klai-knowledge-ingest/knowledge_ingest/models.py`` IngestRequest).

    Raises on any non-2xx so the route layer translates to the correct
    HTTP status (400 / 502 / etc.). Returns the ``artifact_id`` from the
    sink's response.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=60.0,
    ) as client:
        resp = await client.post("/ingest/v1/document", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("artifact_id", "") if isinstance(data, dict) else ""


async def update_kb_visibility(org_id: str, kb_slug: str, visibility: str) -> None:
    """Persist KB visibility to knowledge-ingest (kb_config table + Qdrant backfill).

    Fires-and-logs-on-failure: a visibility sync error must never block the portal
    response. The Qdrant backfill is idempotent — a retry or manual re-call is safe.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
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


# -- SPEC-PORTAL-KENNIS-001: KB sources ---------------------------
#
# These helpers wrap /knowledge/v1/kb/* endpoints introduced for the
# "alles is een bron" UI. Knowledge-ingest returns raw aggregates;
# portal-api enriches connectors with display metadata (name, sync status)
# from its own connectors table before sending to the frontend.


async def get_kb_sources(org_id: str, kb_slug: str) -> dict | None:
    """Fetch grouped sources for a KB.

    Returns ``{"connectors": [{connector_id, items_count, chunks_count}, ...],
    "uploads": [{id, path, content_type, created_at, chunks_count}, ...]}``
    on success, ``None`` on transport / decode failure (caller can fall back
    to an empty UI state).
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.get(
                f"/knowledge/v1/kb/{kb_slug}/sources",
                params={"org_id": org_id},
            )
            resp.raise_for_status()
            data: dict = resp.json()
            return data
    except Exception:
        logger.warning(
            "Could not fetch KB sources from knowledge-ingest (org=%s kb=%s)",
            org_id,
            kb_slug,
            exc_info=True,
        )
        return None


async def list_connector_items(
    org_id: str,
    kb_slug: str,
    connector_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict | None:
    """List artifacts under a connector (drill-down). Returns the raw response or None."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.get(
                f"/knowledge/v1/kb/{kb_slug}/connectors/{connector_id}/items",
                params={"org_id": org_id, "limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            data: dict = resp.json()
            return data
    except Exception:
        logger.warning(
            "Could not fetch connector items (org=%s kb=%s conn=%s)",
            org_id,
            kb_slug,
            connector_id,
            exc_info=True,
        )
        return None


async def list_upload_chunks(
    org_id: str,
    artifact_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict | None:
    """List parent_chunks for a direct-upload artifact. Returns the raw response or None."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.get(
                f"/knowledge/v1/kb/uploads/{artifact_id}/chunks",
                params={"org_id": org_id, "limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            data: dict = resp.json()
            return data
    except Exception:
        logger.warning(
            "Could not fetch upload chunks (org=%s artifact=%s)",
            org_id,
            artifact_id,
            exc_info=True,
        )
        return None


async def reindex_artifact(org_id: str, artifact_id: str) -> None:
    """Re-enqueue a direct-upload artifact for indexing via knowledge-ingest.

    SPEC-PORTAL-KENNIS-002 A4: Portal calls this to trigger a reindex after
    an upload has been set to index_status='pending'. Raises on failure so
    the portal endpoint can return a clear error.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=10.0,
    ) as client:
        resp = await client.post(
            f"/knowledge/v1/artifacts/{artifact_id}/reindex",
            params={"org_id": org_id},
        )
        resp.raise_for_status()


async def delete_kb_upload(
    org_id: str,
    kb_slug: str,
    artifact_id: str,
    user_id: str | None = None,
) -> None:
    """Delete a direct-upload artifact from knowledge-ingest (soft-delete + Qdrant purge).

    SPEC-PORTAL-KENNIS-002 B2: Portal calls this when a user deletes an upload.
    Pass user_id to enforce contributor ownership check on the ingest side
    (the caller can only delete their own uploads). Omit user_id for
    admin/owner deletes (no ownership restriction).
    Raises on failure (404 if not found, 403 if not owner, 5xx on error).
    """
    params: dict = {"org_id": org_id}
    if user_id is not None:
        params["user_id"] = user_id
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=15.0,
    ) as client:
        resp = await client.delete(
            f"/knowledge/v1/kb/{kb_slug}/uploads/{artifact_id}",
            params=params,
        )
        resp.raise_for_status()


async def rename_kb_upload(
    org_id: str,
    kb_slug: str,
    artifact_id: str,
    name: str,
) -> dict:
    """Update the display name for a direct-upload artifact.

    The ingest service stores this as artifact metadata, not as ``path``.
    ``path`` remains the stable document key for Qdrant cleanup/reindex.
    """
    async with httpx.AsyncClient(
        base_url=settings.knowledge_ingest_url,
        headers={
            "X-Internal-Secret": settings.knowledge_ingest_secret,
            "X-Caller-Service": "portal-api",
            **get_trace_headers(),
        },
        timeout=10.0,
    ) as client:
        resp = await client.patch(
            f"/knowledge/v1/kb/{kb_slug}/uploads/{artifact_id}",
            params={"org_id": org_id},
            json={"name": name},
        )
        resp.raise_for_status()
        data: dict = resp.json()
        return data


async def get_chunks_summary(org_id: str, kb_slugs: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Bulk chunk + sources counts per KB.

    Returns ``(chunks_by_kb, sources_by_kb)``. Each map is keyed by KB
    slug; missing keys mean "no data". Returns two empty dicts on failure
    (caller treats as 'unknown' and shows zero).
    """
    if not kb_slugs:
        return {}, {}
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                "X-Caller-Service": "portal-api",
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.post(
                "/knowledge/v1/kb/chunks-summary",
                params={"org_id": org_id},
                json={"kb_slugs": kb_slugs},
            )
            resp.raise_for_status()
            data: dict = resp.json()
            chunks = {str(k): int(v) for k, v in (data.get("chunks_by_kb") or {}).items()}
            # SPEC-PORTAL-SOURCES-RENAME-001 dual-key window: prefer the new
            # `sources_by_kb` key; fall back to the legacy `bronnen_by_kb`
            # so this client keeps rendering counts during a rolling deploy
            # where the ingest container has not yet been bumped. Remove the
            # fallback after every ingest container in fleet ships the new key.
            sources_raw = data.get("sources_by_kb") or data.get("bronnen_by_kb") or {}
            sources = {str(k): int(v) for k, v in sources_raw.items()}
            return chunks, sources
    except Exception:
        logger.warning(
            "Could not fetch chunks-summary from knowledge-ingest (org=%s slugs=%d)",
            org_id,
            len(kb_slugs),
            exc_info=True,
        )
        return {}, {}
