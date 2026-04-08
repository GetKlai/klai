"""
HTTP client for retrieval-api service.
Replaces direct Qdrant queries (narrow) and knowledge_client.py (broad).
"""
import asyncio

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

_TIMEOUT = 10.0
_TAXONOMY_TIMEOUT = 3.0


async def _classify_query(base: str, query: str, kb_slug: str, org_id: str) -> list[int]:
    """POST to knowledge-ingest classify endpoint; returns matched taxonomy node IDs."""
    async with httpx.AsyncClient(timeout=_TAXONOMY_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/ingest/v1/taxonomy/classify",
            json={"org_id": org_id, "kb_slug": kb_slug, "text": query},
        )
        resp.raise_for_status()
        return resp.json().get("taxonomy_node_ids", [])


async def _get_coverage_ratio(base: str, kb_slug: str, org_id: str) -> float:
    """GET coverage-stats from knowledge-ingest; returns fraction of tagged chunks."""
    async with httpx.AsyncClient(timeout=_TAXONOMY_TIMEOUT) as client:
        resp = await client.get(
            f"{base}/ingest/v1/taxonomy/coverage-stats",
            params={"kb_slug": kb_slug, "org_id": org_id},
        )
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total_chunks", 0)
        if total == 0:
            return 0.0
        untagged = data.get("untagged_count", total)
        return (total - untagged) / total


async def _get_taxonomy_filter(
    query: str,
    kb_slug: str,
    org_id: str,
) -> list[int] | None:
    """Classify query against KB taxonomy and return node IDs to filter by.

    Runs coverage check and classification in parallel. Returns None when:
    - Either call fails or times out
    - Coverage is below TAXONOMY_RETRIEVAL_MIN_COVERAGE (default 30%)
    - No node IDs returned
    """
    if not settings.knowledge_ingest_url:
        return None

    base = settings.knowledge_ingest_url.rstrip("/")

    try:
        node_ids, coverage = await asyncio.gather(
            asyncio.wait_for(
                _classify_query(base, query, kb_slug, org_id), timeout=_TAXONOMY_TIMEOUT
            ),
            asyncio.wait_for(
                _get_coverage_ratio(base, kb_slug, org_id), timeout=_TAXONOMY_TIMEOUT
            ),
        )
    except Exception:
        logger.warning("taxonomy_filter_skipped", kb_slug=kb_slug, org_id=org_id)
        return None

    if coverage < settings.taxonomy_retrieval_min_coverage:
        logger.debug("taxonomy_filter_skipped_low_coverage", kb_slug=kb_slug, coverage=coverage)
        return None

    # Node IDs come from the classify endpoint, which fetches the current taxonomy
    # immediately before classifying. It can only return IDs that exist at call time.
    # Stale IDs are not possible without a sub-second race condition (node deleted
    # between classify response and this line). Even then, Qdrant chunks retain their
    # taxonomy_node_ids after portal node deletion — MatchAny([stale_id]) still
    # returns the correctly tagged content.
    return node_ids if node_ids else None


async def retrieve_narrow(
    question: str,
    notebook_id: str,
    tenant_id: str,
    top_k: int = 8,
) -> list[dict]:
    """
    Narrow retrieval: notebook-scoped chunks from klai_focus via retrieval-api.
    Returns list of {chunk_id, content, score, source_name, origin, metadata}.
    """
    if not settings.retrieval_api_url:
        logger.warning("RETRIEVAL_API_URL not set, narrow retrieval returns empty")
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.retrieval_api_url}/retrieve",
                json={
                    "query": question,
                    "org_id": tenant_id,
                    "scope": "notebook",
                    "notebook_id": notebook_id,
                    "top_k": top_k,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [_to_chunk(c, origin="focus") for c in data.get("chunks", [])]
    except Exception as exc:
        logger.error("retrieval-api narrow failed: %s", exc)
        return []


async def retrieve_broad(
    question: str,
    notebook_id: str,
    tenant_id: str,
    top_k: int = 8,
    kb_slug: str | None = None,
) -> list[dict]:
    """
    Broad retrieval: Focus + KB chunks via retrieval-api broad scope.
    Returns list of {chunk_id, content, score, source_name, origin, metadata}.

    When kb_slug is provided, applies taxonomy-aware filtering: classifies the query
    against the KB's taxonomy and passes matching node IDs to the retrieval-api.
    Falls back to unfiltered retrieval on any classification error.
    """
    if not settings.retrieval_api_url:
        logger.warning("RETRIEVAL_API_URL not set, broad retrieval returns empty")
        return []

    taxonomy_node_ids: list[int] | None = None
    if kb_slug:
        taxonomy_node_ids = await _get_taxonomy_filter(
            query=question,
            kb_slug=kb_slug,
            org_id=tenant_id,
        )

    try:
        request_body: dict = {
            "query": question,
            "org_id": tenant_id,
            "scope": "broad",
            "notebook_id": notebook_id,
            "top_k": top_k,
        }
        if taxonomy_node_ids is not None:
            request_body["taxonomy_node_ids"] = taxonomy_node_ids

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.retrieval_api_url}/retrieve",
                json=request_body,
            )
            resp.raise_for_status()
            data = resp.json()
            chunks = data.get("chunks", [])
            # Infer origin from whether chunk came from focus or KB
            # retrieval-api broad merges both; we can't distinguish post-merge
            # Mark all as "broad" so chat.py uses BROAD_SYSTEM_PROMPT
            return [_to_chunk(c, origin="broad") for c in chunks]
    except Exception as exc:
        logger.error("retrieval-api broad failed: %s", exc)
        return []


def _to_chunk(c: dict, origin: str) -> dict:
    """Convert retrieval-api ChunkResult to research-api internal chunk format."""
    text = c.get("text", "")
    prefix = c.get("context_prefix", "")
    content = f"{prefix}\n{text}".strip() if prefix else text
    return {
        "chunk_id": c.get("chunk_id", ""),
        "source_id": c.get("artifact_id") or "unknown",
        "content": content,
        "metadata": {
            "source_ref": c.get("source_ref"),
            "source_connector_id": c.get("source_connector_id"),
            "source_url": c.get("source_url"),
        },
        "score": float(c.get("reranker_score") or c.get("score") or 0.0),
        "source_name": c.get("title") or c.get("artifact_id") or "Knowledge Base",
        "origin": origin,
    }
