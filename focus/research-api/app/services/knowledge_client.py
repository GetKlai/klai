"""
HTTP client for Klai Knowledge base retrieval via knowledge-ingest service.
Uses the actual knowledge-ingest API contract:
  POST /knowledge/v1/retrieve
  Request: {org_id, query, top_k, kb_slugs?, user_id?}
  Response: {chunks: [{text, source, score, metadata, ...}]}
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 3.0


async def retrieve_knowledge(query: str, org_id: str, top_k: int = 5) -> list[dict]:
    """
    Retrieve knowledge base results from knowledge-ingest service.
    Returns list of {content, source_name, score, metadata, origin}.
    On any error, logs warning and returns empty list (graceful degradation).
    """
    if not settings.knowledge_ingest_url:
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.knowledge_ingest_url}/knowledge/v1/retrieve",
                json={"query": query, "org_id": org_id, "top_k": top_k},
            )
            resp.raise_for_status()
            data = resp.json()
            chunks = data.get("chunks", [])
            return [
                {
                    "content": c.get("text", ""),
                    "source_name": c.get("source", "Knowledge Base"),
                    "score": float(c.get("score", 0.0)),
                    "metadata": c.get("metadata", {}),
                    "origin": "kb",
                }
                for c in chunks
            ]
    except Exception as exc:
        logger.warning("knowledge-ingest retrieve failed: %s", exc)
        return []
