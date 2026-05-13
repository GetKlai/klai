"""Reranker service: re-score candidates using a cross-encoder via Infinity.
Infinity runs on gpu-01 at port 7998, tunneled to 172.18.0.1:7998 on core-01.
Uses /v1/rerank API with bge-reranker-v2-m3.
Do NOT confuse with TEI (port 7997), which is the separate dense-embedding service.
"""

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
    """Rerank candidates using the Infinity reranker endpoint.

    On failure (timeout, HTTP error), falls back to returning
    ``candidates[:top_k]`` with ``reranker_score=None``.
    """
    if not candidates:
        return []

    # SPEC-RAG-CONTEXTUAL-001 parity: feed the cross-encoder the same
    # context_prefix + chunk_text combination that the embedding model
    # saw at index time. Without this the reranker scores chunks on raw
    # body alone — context-prefix-driven semantics (which document /
    # which section / which terminology) is lost from ranking.
    # Falls back to plain text when context_prefix is null (legacy
    # chunks pre-CONTEXTUAL-001).
    def _passage(c: dict) -> str:
        prefix = c.get("context_prefix") or ""
        text = c.get("text") or ""
        return f"{prefix}\n\n{text}".strip() if prefix else text

    passages = [_passage(c) for c in candidates]

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
    except Exception:
        # F6 audit cleanup (TRY401): exc_info=True preserves traceback.
        logger.warning("Reranker call failed, falling back to Qdrant scores", exc_info=True)
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
