"""POST /retrieve endpoint -- structured retrieval pipeline."""

from __future__ import annotations

import asyncio
import copy
import math
import os
import time

import structlog
from fastapi import APIRouter, HTTPException, Request

from retrieval_api.config import settings
from retrieval_api.metrics import (
    retrieval_chunks_total,
    retrieval_requests_total,
    step_latency_seconds,
)
from retrieval_api.middleware.auth import verify_body_identity
from retrieval_api.models import ChunkResult, RetrieveMetadata, RetrieveRequest, RetrieveResponse
from retrieval_api.quality_boost import quality_boost
from retrieval_api.services import coreference, evidence_tier, gate, graph_search, reranker, search
from retrieval_api.services.diversity import source_aware_select
from retrieval_api.services.events import emit_event
from retrieval_api.services.router import fetch_source_catalog, route_to_sources
from retrieval_api.services.tei import embed_single, embed_sparse
from retrieval_api.util.payload import payload_list

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
async def retrieve(req: RetrieveRequest, request: Request) -> RetrieveResponse:
    # --- Validation ---
    if req.scope in ("personal", "both") and not req.user_id:
        raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")
    if req.scope == "notebook" and not req.notebook_id:
        raise HTTPException(status_code=400, detail="notebook_id required for scope=notebook")
    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-5.2: notebook scope requires user_id so the
    # personal-vs-team visibility gate can apply. Without user_id, the personal
    # leg of _notebook_filter cannot fire and personal chunks would silently
    # disappear from results — fail loud rather than silent.
    if req.scope == "notebook" and not req.user_id:
        raise HTTPException(status_code=400, detail="missing_user_id_for_personal_scope")

    # SPEC-SEC-010 REQ-3 + SPEC-SEC-IDENTITY-ASSERT-001 REQ-4: cross-user /
    # cross-org guard. JWT callers are matched against their JWT claims;
    # internal-secret callers are re-verified against portal-api so the
    # internal-secret bypass no longer admits arbitrary body identities.
    # On allow this also pins request.state.verified_caller, which is what
    # emit_event below sources for product_events integrity (REQ-6).
    await verify_body_identity(request, req.org_id, req.user_id)

    t0 = time.perf_counter()
    # @MX:NOTE: [AUTO] Shadow log for parameter tuning (SPEC-KB-021 Change 4).
    # decision_record accumulates timing + decision data throughout the pipeline
    # and is emitted as retrieval_decision_record at the end of the request.
    decision_record: dict = {}

    # 1. Coreference resolution
    t_coref = time.perf_counter()
    query_resolved = await coreference.resolve(req.query, req.conversation_history)
    coref_ms = (time.perf_counter() - t_coref) * 1000
    step_latency_seconds.labels(step="coref").observe(time.perf_counter() - t_coref)
    decision_record["coreference_rewrite"] = {"original": req.query, "resolved": query_resolved}
    decision_record["coreference_ms"] = round(coref_ms, 1)

    # 2. Embed resolved query (dense + sparse in parallel)
    t_embed = time.perf_counter()
    query_vector, sparse_vector = await asyncio.gather(
        embed_single(query_resolved),
        embed_sparse(query_resolved),
    )
    embed_ms = (time.perf_counter() - t_embed) * 1000
    step_latency_seconds.labels(step="embed").observe(time.perf_counter() - t_embed)
    decision_record["embedding_ms"] = round(embed_ms, 1)

    # 3. Gate check
    bypassed, gate_margin = await gate.should_bypass(query_vector)

    decision_record["gate_margin"] = round(gate_margin, 4) if gate_margin is not None else None
    decision_record["gate_bypassed"] = bypassed
    decision_record["gate_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    chunks_out: list[ChunkResult] = []
    candidates_retrieved = 0
    reranked_to = 0
    qdrant_ms: float | None = None
    rerank_ms: float | None = None
    graph_results_count = 0
    graph_search_ms: float | None = None
    link_expand_ms: float | None = None
    link_expand_count = 0

    # 3b. Query router — identifies relevant sources for post-rerank selection
    router_meta: dict = {"router_decision": None, "router_layer_used": "skipped"}
    router_selected: set[str] | None = None
    if (
        req.kb_slugs is None
        and settings.router_enabled
        and req.scope in ("org", "both")
        and not bypassed
    ):
        source_label_catalog = await fetch_source_catalog(req.org_id)
        if len(source_label_catalog) >= settings.router_min_source_label_count:
            routing = await route_to_sources(
                query_resolved=query_resolved,
                query_vector=query_vector,
                org_id=req.org_id,
                source_label_catalog=source_label_catalog,
                margin_single=settings.router_margin_single,
                margin_dual=settings.router_margin_dual,
                llm_fallback=settings.router_llm_fallback,
                centroid_ttl_seconds=settings.router_centroid_ttl_seconds,
            )
            if routing.selected_source_labels:
                router_selected = set(routing.selected_source_labels)
            router_meta = {
                "router_decision": routing.selected_source_labels,
                "router_layer_used": routing.layer_used,
                "router_margin": routing.margin,
                "router_centroid_cache_hit": routing.cache_hit,
            }
    decision_record["router"] = router_meta

    if not bypassed:
        # 4. Search — Qdrant + Graphiti in parallel (AC-5)
        t_qdrant = time.perf_counter()
        qdrant_coro = search.hybrid_search(
            query_vector, req, settings.retrieval_candidates, sparse_vector
        )

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
        decision_record["search_ms"] = round(qdrant_ms, 1)

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
        decision_record["search_candidates_count"] = candidates_retrieved

        # 4b. Link expansion (SPEC-CRAWLER-003 R14-R16)
        if settings.link_expand_enabled and req.scope != "notebook" and raw_results:
            t_expand = time.perf_counter()
            seed_chunks = raw_results[: settings.link_expand_seed_k]
            candidate_urls: list[str] = []
            seen_urls: set[str] = set()
            for chunk in seed_chunks:
                for url in payload_list(chunk, "links_to"):
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
            decision_record["rerank_ms"] = round(rerank_ms, 1)
            decision_record["reranker_scores_top5"] = [
                r.get("reranker_score") or r.get("score", 0) for r in reranked[:5]
            ]
        else:
            reranked = raw_results[: req.top_k]
            reranked_to = len(reranked)

        # 5b. Source-aware selection (SPEC-KB-021)
        # Replaces separate router + quota: uses reranker scores to decide.
        if settings.source_quota_enabled:
            reranked, source_meta = source_aware_select(
                reranked,
                query_resolved,
                top_n=req.top_k,
                max_per_source=settings.source_quota_max_per_source,
                router_selected=router_selected,
            )
        else:
            source_meta = {
                "source_select_mode": "disabled",
                "source_counts": {},
                "mentioned_sources": [],
            }
        decision_record["source_select"] = source_meta

        # 5c. Quality score boost (SPEC-KB-015 REQ-KB-015-19,20,21)
        reranked = quality_boost(reranked)
        decision_record["quality_boost_applied"] = any(
            r.get("feedback_count", 0) >= 3 for r in reranked
        )

        # @MX:NOTE: [AUTO] Shadow mode (R9): runs evidence scoring on every
        # request but serves flat results. Diffs logged as shadow_eval to
        # VictoriaLogs for offline analysis.
        # @MX:NOTE: Set EVIDENCE_SHADOW_MODE=false to activate evidence-tier
        # scoring for users.
        # @MX:SPEC: SPEC-EVIDENCE-001 R9. Disable shadow mode after RAGAS
        # validation confirms improvement.
        # 6. Evidence tier scoring + U-shape ordering (SPEC-EVIDENCE-001, R7)
        shadow_mode = os.environ.get("EVIDENCE_SHADOW_MODE", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        decision_record["evidence_shadow_mode"] = shadow_mode
        scored = evidence_tier.apply(copy.deepcopy(reranked))

        if shadow_mode:
            # R9: Log shadow results but serve original flat scoring
            logger.info(
                "shadow_eval",
                flat_top_chunk_ids=[c["chunk_id"] for c in reranked[:5]],
                evidence_top_chunk_ids=[c["chunk_id"] for c in scored[:5]],
                score_deltas=[
                    round(
                        scored[i].get("final_score", 0)
                        - (reranked[i].get("reranker_score") or reranked[i]["score"]),
                        4,
                    )
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
                source_ref=r.get("source_ref"),
                source_connector_id=r.get("source_connector_id"),
                source_url=r.get("source_url"),
                kb_slug=r.get("kb_slug"),
                source_label=r.get("source_label"),
                title=r.get("title"),
                image_urls=payload_list(r, "image_urls") or None,
            )
            for r in serving
        ]

    retrieval_ms = (time.perf_counter() - t0) * 1000
    step_latency_seconds.labels(step="total").observe(retrieval_ms / 1000)
    retrieval_requests_total.labels(scope=req.scope, bypassed=str(bypassed).lower()).inc()
    retrieval_chunks_total.labels(scope=req.scope).observe(len(chunks_out))

    decision_record["total_ms"] = round(retrieval_ms, 1)
    try:
        logger.info(
            "retrieval_decision_record",
            org_id=req.org_id,
            scope=req.scope,
            **decision_record,
        )
    except Exception:
        logger.exception("decision_record_emit_failed")

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

    # SPEC-GRAFANA-METRICS: knowledge.queried event (skip notebook scope — Focus has its own).
    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-6: tenant_id / user_id MUST come from
    # the verified-caller pin set by verify_body_identity, never from the
    # request body. Body fields are caller-supplied; product_events is a
    # business-metrics contract whose integrity we cannot let any caller
    # poison.
    if req.scope != "notebook":
        verified = getattr(request.state, "verified_caller", None)
        if verified is not None:
            emit_event(
                "knowledge.queried",
                tenant_id=verified.org_id,
                user_id=verified.user_id,
                properties={
                    "scope": req.scope,
                    "had_results": len(chunks_out) > 0,
                    "result_count": len(chunks_out),
                },
            )
        else:
            # Defense in depth: should be unreachable because verify_body_identity
            # always pins the verified tuple on the success path. If we see this
            # log line in production, a new code path is bypassing the guard.
            logger.warning(
                "product_event_skipped_no_identity",
                event_type="knowledge.queried",
                scope=req.scope,
                path=request.url.path,
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
