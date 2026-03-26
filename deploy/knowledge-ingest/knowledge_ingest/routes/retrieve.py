"""
Retrieve route:
  POST /knowledge/v1/retrieve — hybrid semantic search for LiteLLM hook

DEPRECATED: This endpoint is superseded by retrieval-api POST /retrieve (SPEC-KB-008).
Will be removed in KB-010 or later. New consumers should use retrieval-api directly.
"""
import asyncio
import logging

import httpx
from fastapi import APIRouter

from knowledge_ingest import embedder, qdrant_store, sparse_embedder
from knowledge_ingest.config import settings
from knowledge_ingest.db import get_pool
from knowledge_ingest.models import ChunkResult, RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_ARTIFACT_FIELDS = (
    "provenance_type", "assertion_mode", "synthesis_depth", "confidence",
    "belief_time_start", "belief_time_end",
)


async def _rerank(query: str, results: list[dict], top_k: int) -> list[dict]:
    """
    Call Infinity reranker to reorder results by relevance.
    Returns the top_k results sorted by reranker score.
    Falls back to the original list on any error or if reranker is not configured.
    """
    if not settings.reranker_url:
        return results[:top_k]

    documents = [r["text"] for r in results]
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                f"{settings.reranker_url}/v1/rerank",
                json={
                    "model": settings.reranker_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Reranker call failed, falling back to Qdrant scores: %s", exc)
        return results[:top_k]

    reranked = sorted(data["results"], key=lambda x: x["relevance_score"], reverse=True)
    return [results[item["index"]] for item in reranked[:top_k]]


@router.post(
    "/knowledge/v1/retrieve",
    response_model=RetrieveResponse,
    deprecated=True,
    summary="[DEPRECATED] Use retrieval-api POST /retrieve instead (SPEC-KB-008)",
)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    dense_vec, sparse_vec = await asyncio.gather(
        embedder.embed_one(req.query),
        sparse_embedder.embed_sparse(req.query),
    )
    if sparse_vec is None:
        logger.warning("sparse_sidecar_unavailable_at_query, query_prefix=%s", req.query[:50])

    # Fetch more candidates when reranker is enabled so it has a meaningful pool
    candidate_k = max(req.top_k * 4, 20) if settings.reranker_url else req.top_k
    results = await qdrant_store.search(
        org_id=req.org_id,
        query_vector=dense_vec,
        top_k=candidate_k,
        kb_slugs=req.kb_slugs,
        user_id=req.user_id,
        sparse_vector=sparse_vec,
        sparse_weight=req.sparse_weight,
    )

    # Rerank if configured; otherwise trim to top_k
    results = await _rerank(req.query, results, req.top_k)

    # Enrich chunks with PostgreSQL artifact metadata
    artifact_ids = list({
        r["metadata"].get("artifact_id")
        for r in results
        if r["metadata"].get("artifact_id")
    })
    artifact_meta: dict = {}
    if artifact_ids:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id::text, provenance_type, assertion_mode, synthesis_depth, confidence, "
            "       belief_time_start, belief_time_end "
            "FROM knowledge.artifacts WHERE id::text = ANY($1::text[])",
            artifact_ids,
        )
        artifact_meta = {r["id"]: dict(r) for r in rows}

    chunks = []
    for r in results:
        aid = r["metadata"].get("artifact_id")
        meta = dict(r.get("metadata", {}))
        pg_data = artifact_meta.get(aid, {}) if aid else {}
        chunk = ChunkResult(
            text=r["text"],
            source=r["source"],
            score=r["score"],
            metadata=meta,
            artifact_id=aid,
            provenance_type=pg_data.get("provenance_type") or meta.get("provenance_type"),
            assertion_mode=pg_data.get("assertion_mode"),
            synthesis_depth=pg_data.get("synthesis_depth"),
            confidence=pg_data.get("confidence") or meta.get("confidence"),
        )
        chunks.append(chunk)

    logger.debug("Retrieved %d chunks for org %s (query len=%d)", len(chunks), req.org_id, len(req.query))
    return RetrieveResponse(chunks=chunks)
