"""POST /retrieve endpoint -- structured retrieval pipeline."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from retrieval_api.config import settings
from retrieval_api.models import ChunkResult, RetrieveMetadata, RetrieveRequest, RetrieveResponse
from retrieval_api.services import coreference, gate, reranker, search
from retrieval_api.services.tei import embed_single

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    # --- Validation ---
    if req.scope in ("personal", "both") and not req.user_id:
        raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")
    if req.scope == "notebook" and not req.notebook_id:
        raise HTTPException(status_code=400, detail="notebook_id required for scope=notebook")

    t0 = time.perf_counter()

    # 1. Coreference resolution
    query_resolved = await coreference.resolve(req.query, req.conversation_history)

    # 2. Embed resolved query
    query_vector = await embed_single(query_resolved)

    # 3. Gate check
    bypassed, gate_margin = await gate.should_bypass(query_vector)

    chunks_out: list[ChunkResult] = []
    candidates_retrieved = 0
    reranked_to = 0
    rerank_ms: float | None = None

    if not bypassed:
        # 4. Search
        raw_results = await search.hybrid_search(
            query_vector, req, settings.retrieval_candidates
        )
        candidates_retrieved = len(raw_results)

        # 5. Rerank (skip for notebook scope)
        if req.scope != "notebook" and raw_results:
            t_rerank = time.perf_counter()
            reranked = await reranker.rerank(query_resolved, raw_results, req.top_k)
            rerank_ms = (time.perf_counter() - t_rerank) * 1000
            reranked_to = len(reranked)
        else:
            reranked = raw_results[: req.top_k]
            reranked_to = len(reranked)

        # 6. Build ChunkResult objects
        chunks_out = [
            ChunkResult(
                chunk_id=r["chunk_id"],
                artifact_id=r.get("artifact_id"),
                content_type=r.get("content_type"),
                text=r["text"],
                context_prefix=r.get("context_prefix"),
                score=r["score"],
                reranker_score=r.get("reranker_score"),
                scope=r.get("scope"),
                valid_at=r.get("valid_at"),
                invalid_at=r.get("invalid_at"),
            )
            for r in reranked
        ]

    retrieval_ms = (time.perf_counter() - t0) * 1000

    # Structured logging (AC-11)
    logger.info(
        "retrieve",
        extra={
            "org_id": req.org_id,
            "scope": req.scope,
            "top_k": req.top_k,
            "candidates_retrieved": candidates_retrieved,
            "retrieval_ms": round(retrieval_ms, 1),
            "rerank_ms": round(rerank_ms, 1) if rerank_ms is not None else None,
            "gate_margin": round(gate_margin, 4) if gate_margin is not None else None,
            "retrieval_bypassed": bypassed,
        },
    )

    return RetrieveResponse(
        query_resolved=query_resolved,
        retrieval_bypassed=bypassed,
        chunks=chunks_out,
        metadata=RetrieveMetadata(
            candidates_retrieved=candidates_retrieved,
            reranked_to=reranked_to,
            retrieval_ms=round(retrieval_ms, 1),
            rerank_ms=round(rerank_ms, 1) if rerank_ms is not None else None,
            gate_margin=round(gate_margin, 4) if gate_margin is not None else None,
        ),
    )
