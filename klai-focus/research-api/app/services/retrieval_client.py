"""
HTTP client for retrieval-api service.
Replaces direct Qdrant queries (narrow) and knowledge_client.py (broad).
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


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
) -> list[dict]:
    """
    Broad retrieval: Focus + KB chunks via retrieval-api broad scope.
    Returns list of {chunk_id, content, score, source_name, origin, metadata}.
    """
    if not settings.retrieval_api_url:
        logger.warning("RETRIEVAL_API_URL not set, broad retrieval returns empty")
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.retrieval_api_url}/retrieve",
                json={
                    "query": question,
                    "org_id": tenant_id,
                    "scope": "broad",
                    "notebook_id": notebook_id,
                    "top_k": top_k,
                },
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
        },
        "score": float(c.get("reranker_score") or c.get("score") or 0.0),
        "source_name": c.get("title") or c.get("artifact_id") or "Knowledge Base",
        "origin": origin,
    }
