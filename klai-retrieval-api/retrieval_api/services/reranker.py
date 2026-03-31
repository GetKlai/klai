"""Reranker service: re-score candidates using a cross-encoder via TEI."""

from __future__ import annotations

import logging

import httpx

from retrieval_api.config import settings

logger = logging.getLogger(__name__)


async def rerank(
    query: str,
    candidates: list[dict],
    top_k: int,
) -> list[dict]:
    """Rerank candidates using the TEI reranker endpoint.

    On failure (timeout, HTTP error), falls back to returning
    ``candidates[:top_k]`` with ``reranker_score=None``.
    """
    if not candidates:
        return []

    passages = [c["text"] for c in candidates]

    try:
        async with httpx.AsyncClient(timeout=settings.reranker_timeout) as client:
            resp = await client.post(
                f"{settings.infinity_reranker_url}/v1/rerank",
                json={
                    "model": "bge-reranker-v2-m3",
                    "query": query,
                    "documents": passages,
                    "top_n": top_k,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Reranker call failed, falling back to Qdrant scores: %s", exc)
        fallback = candidates[:top_k]
        for c in fallback:
            c["reranker_score"] = None
        return fallback

    # data["results"] is a list of {"index": int, "score"|"relevance_score": float}
    # TEI returns "score"; Infinity /v1/rerank returns "relevance_score"
    results_map = data.get("results", data) if isinstance(data, dict) else data
    if isinstance(results_map, dict):
        results_map = results_map.get("results", [])

    # Build reranked list sorted by reranker score descending
    reranked: list[dict] = []
    for item in results_map:
        idx = item["index"]
        if idx < len(candidates):
            candidate = candidates[idx].copy()
            candidate["reranker_score"] = item.get("score", item.get("relevance_score"))
            reranked.append(candidate)

    reranked.sort(key=lambda x: x.get("reranker_score", 0), reverse=True)
    return reranked[:top_k]
