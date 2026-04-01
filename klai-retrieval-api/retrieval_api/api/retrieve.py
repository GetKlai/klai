"""POST /retrieve endpoint -- structured retrieval pipeline."""

from __future__ import annotations

import asyncio
import copy
import math
import os
import time

import structlog
from fastapi import APIRouter, HTTPException

from retrieval_api.config import settings
from retrieval_api.metrics import retrieval_chunks_total, retrieval_requests_total, step_latency_seconds
from retrieval_api.models import ChunkResult, RetrieveMetadata, RetrieveRequest, RetrieveResponse
from retrieval_api.services import coreference, evidence_tier, gate, graph_search, reranker, search
from retrieval_api.services.tei import embed_single, embed_sparse

logger = structlog.get_logger(__name__)

router = APIRouter()


def _rrf_merge(qdrant_results: list[dict], graph_results: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion merge of two ranked result lists (AC-5)."""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for rank, result in enumerate(qdrant_results):
        cid = result["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        items[cid] = result

    for rank, result in enumerate(graph_results):
        cid = result["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        if cid not in items:
            items[cid] = result

    merged = sorted(items.values(), key=lambda r: scores[r["chunk_id"]], reverse=True)
    for result in merged:
        result["score"] = scores[result["chunk_id"]]
    return merged


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    # --- Validation ---
    if req.scope in ("personal", "both") and not req.user_id:
        raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")
    if req.scope == "notebook" and not req.notebook_id:
        raise HTTPException(status_code=400, detail="notebook_id required for scope=notebook")

    t0 = time.perf_counter()

    # 1. Coreference resolution
    t_coref = time.perf_counter()
    query_resolved = await coreference.resolve(req.query, req.conversation_history)
    coref_ms = (time.perf_counter() - t_coref) * 1000
    step_latency_seconds.labels(step="coref").observe((time.perf_counter() - t_coref))

    # 2. Embed resolved query (dense + sparse in parallel)
    t_embed = time.perf_counter()
    query_vector, sparse_vector = await asyncio.gather(
        embed_single(query_resolved),
        embed_sparse(query_resolved),
    )
    embed_ms = (time.perf_counter() - t_embed) * 1000
    step_latency_seconds.labels(step="embed").observe((time.perf_counter() - t_embed))

    # 3. Gate check
    bypassed, gate_margin = await gate.should_bypass(query_vector)

    chunks_out: list[ChunkResult] = []
    candidates_retrieved = 0
    reranked_to = 0
    qdrant_ms: float | None = None
    rerank_ms: float | None = None
    graph_results_count = 0
    graph_search_ms: float | None = None
    link_expand_ms: float | None = None
    link_expand_count = 0

    if not bypassed:
        # 4. Search — Qdrant + Graphiti in parallel (AC-5)
        t_qdrant = time.perf_counter()
        qdrant_coro = search.hybrid_search(query_vector, req, settings.retrieval_candidates, sparse_vector)

        graph_task: asyncio.Task[list[dict]] | None = None
        t_graph: float | None = None
        if req.scope != "notebook" and settings.graphiti_enabled:  # AC-6: skip notebook
            t_graph = time.perf_counter()
            graph_task = asyncio.create_task(
                graph_search.search(query_resolved, req.org_id, top_k=20)
            )

        raw_results = await qdrant_coro
        qdrant_ms = (time.perf_counter() - t_qdrant) * 1000
        step_latency_seconds.labels(step="qdrant").observe(time.perf_counter() - t_qdrant)

        if graph_task is not None and t_graph is not None:
            try:
                graph_results = await graph_task
                graph_search_ms = (time.perf_counter() - t_graph) * 1000
                step_latency_seconds.labels(step="graph").observe(graph_search_ms / 1000)
                graph_results_count = len(graph_results)
                if graph_results:
                    raw_results = _rrf_merge(raw_results, graph_results)
            except Exception as exc:
                logger.warning("Graph search task failed", error=str(exc))

        candidates_retrieved = len(raw_results)

        # 4b. Link expansion (SPEC-CRAWLER-003 R14-R16)
        if settings.link_expand_enabled and req.scope != "notebook" and raw_results:
            t_expand = time.perf_counter()
            seed_chunks = raw_results[: settings.link_expand_seed_k]
            candidate_urls: list[str] = []
            seen_urls: set[str] = set()
            for chunk in seed_chunks:
                for url in (chunk.get("links_to") or []):
                    if url not in seen_urls:
                        seen_urls.add(url)
                        candidate_urls.append(url)
                    if len(candidate_urls) >= settings.link_expand_max_urls:
                        break
                if len(candidate_urls) >= settings.link_expand_max_urls:
                    break

            if candidate_urls:
                expansion_chunks = await search.fetch_chunks_by_urls(
                    candidate_urls, req, settings.link_expand_candidates
                )
                existing_ids = {r["chunk_id"] for r in raw_results}
                new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]
                link_expand_count = len(new_chunks)
                raw_results = raw_results + new_chunks

            link_expand_ms = (time.perf_counter() - t_expand) * 1000
            step_latency_seconds.labels(step="link_expand").observe(link_expand_ms / 1000)
            logger.debug(
                "link_expand",
                seed_k=len(seed_chunks),
                candidate_urls=len(candidate_urls),
                new_chunks=link_expand_count,
            )

        # 4c. Authority boost (SPEC-CRAWLER-003 R17)
        if settings.link_expand_enabled and raw_results:
            for r in raw_results:
                incoming = r.get("incoming_link_count") or 0
                if incoming > 0:
                    r["score"] = r["score"] + settings.link_authority_boost * math.log(1 + incoming)

        # 5. Rerank (skip for notebook scope or when reranker disabled)
        if req.scope != "notebook" and raw_results and settings.reranker_enabled:
            t_rerank = time.perf_counter()
            rerank_input = raw_results[: settings.reranker_candidates]
            reranked = await reranker.rerank(query_resolved, rerank_input, req.top_k)
            rerank_ms = (time.perf_counter() - t_rerank) * 1000
            step_latency_seconds.labels(step="rerank").observe(rerank_ms / 1000)
            reranked_to = len(reranked)
        else:
            reranked = raw_results[: req.top_k]
            reranked_to = len(reranked)

        # @MX:NOTE: [AUTO] Shadow mode (R9): runs evidence scoring on every request but serves
        # @MX:NOTE: flat results. Diffs logged as shadow_eval to VictoriaLogs for offline analysis.
        # @MX:NOTE: Set EVIDENCE_SHADOW_MODE=false to activate evidence-tier scoring for users.
        # @MX:SPEC: SPEC-EVIDENCE-001 R9. Disable shadow mode after RAGAS validation confirms improvement.
        # 6. Evidence tier scoring + U-shape ordering (SPEC-EVIDENCE-001, R7)
        shadow_mode = os.environ.get("EVIDENCE_SHADOW_MODE", "true").lower() in (
            "true", "1", "yes",
        )
        scored = evidence_tier.apply(copy.deepcopy(reranked))

        if shadow_mode:
            # R9: Log shadow results but serve original flat scoring
            logger.info(
                "shadow_eval",
                flat_top_chunk_ids=[c["chunk_id"] for c in reranked[:5]],
                evidence_top_chunk_ids=[c["chunk_id"] for c in scored[:5]],
                score_deltas=[
                    round(scored[i].get("final_score", 0) - (reranked[i].get("reranker_score") or reranked[i]["score"]), 4)
                    for i in range(min(5, len(reranked)))
                ],
            )
            serving = reranked
        else:
            serving = scored

        # 7. Build ChunkResult objects
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
                ingested_at=r.get("ingested_at"),
                assertion_mode=r.get("assertion_mode"),
                final_score=r.get("final_score"),
                evidence_tier_metadata=r.get("evidence_tier_metadata"),
            )
            for r in serving
        ]

    retrieval_ms = (time.perf_counter() - t0) * 1000
    step_latency_seconds.labels(step="total").observe(retrieval_ms / 1000)
    retrieval_requests_total.labels(scope=req.scope, bypassed=str(bypassed).lower()).inc()
    retrieval_chunks_total.labels(scope=req.scope).observe(len(chunks_out))

    logger.info(
        "retrieve",
        org_id=req.org_id,
        scope=req.scope,
        top_k=req.top_k,
        candidates_retrieved=candidates_retrieved,
        graph_results_count=graph_results_count,
        coref_ms=round(coref_ms, 1),
        embed_ms=round(embed_ms, 1),
        qdrant_ms=round(qdrant_ms, 1) if qdrant_ms is not None else None,
        retrieval_ms=round(retrieval_ms, 1),
        graph_search_ms=round(graph_search_ms, 1) if graph_search_ms is not None else None,
        rerank_ms=round(rerank_ms, 1) if rerank_ms is not None else None,
        link_expand_ms=round(link_expand_ms, 1) if link_expand_ms is not None else None,
        link_expand_count=link_expand_count,
        gate_margin=round(gate_margin, 4) if gate_margin is not None else None,
        retrieval_bypassed=bypassed,
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
            graph_results_count=graph_results_count,
            graph_search_ms=round(graph_search_ms, 1) if graph_search_ms is not None else None,
        ),
    )
