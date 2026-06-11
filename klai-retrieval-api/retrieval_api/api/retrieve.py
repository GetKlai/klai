"""POST /retrieve endpoint -- structured retrieval pipeline."""

from __future__ import annotations

import asyncio
import copy
import math
import os
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from klai_kb_slugs import personal_kb_slug

from retrieval_api.api.decision_log import _evidence_pack_decision_sources
from retrieval_api.api.page_context import _apply_page_context_boost
from retrieval_api.api.ranking import (
    _apply_link_expand_boost,
    _compute_confidence_band,
    _rrf_merge,
)
from retrieval_api.config import settings
from retrieval_api.metrics import (
    quality_floor_filtered_total,
    retrieval_chunks_total,
    retrieval_confidence_band_total,
    retrieval_graph_top_k_total,
    retrieval_link_expand_top_k_total,
    retrieval_requests_total,
    step_latency_seconds,
    telemetry_level_decisions_total,
)
from retrieval_api.middleware.auth import AuthContext, require_scope, verify_body_identity
from retrieval_api.models import (
    ChunkResult,
    ConfidenceBand,
    RetrieveMetadata,
    RetrieveRequest,
    RetrieveResponse,
)
from retrieval_api.quality_boost import quality_boost
from retrieval_api.quality_floor import filter_quality_floor
from retrieval_api.services import coreference, evidence_tier, gate, graph_search, reranker, search
from retrieval_api.services.diversity import source_aware_select
from retrieval_api.services.events import emit_event
from retrieval_api.services.evidence_pack import build_evidence_pack
from retrieval_api.services.features import extract_features
from retrieval_api.services.router import fetch_source_catalog, route_to_sources
from retrieval_api.services.tei import embed_single, embed_sparse
from retrieval_api.services.telemetry import write_shadow
from retrieval_api.services.tenant_telemetry import get_canonical_level, resolve_effective_level
from retrieval_api.util.payload import payload_list

logger = structlog.get_logger(__name__)

router = APIRouter()

# SPEC-SEC-SERVICE-AUTH-001 REQ-3: scope required for the /retrieve endpoint.
# Internal-secret callers are bypassed during Phase B/C migration; once Phase D
# removes the legacy auth path, only callers presenting a JWT with this scope
# will reach this endpoint. Granted to: svc-litellm, svc-knowledge-mcp,
# svc-portal-api.
_RETRIEVAL_QUERY_SCOPE = "klai:internal:retrieval:query"
# Module-level singleton — avoids ruff B008 ("Depends in default arg") and
# is the FastAPI-recommended pattern for repeated dependencies.
_REQUIRE_RETRIEVAL_SCOPE = Depends(require_scope(_RETRIEVAL_QUERY_SCOPE))


def _caller_pre_resolved(req: RetrieveRequest) -> bool:
    """Whether the caller already resolved coreference for this request.

    True when a non-empty ``raw_query`` is present AND differs from ``query`` —
    the litellm hook's contract: it sends the rewritten query as ``query`` and
    the user's pre-rewrite text as ``raw_query``. knowledge-mcp sends
    ``raw_query == query`` (no rewrite) and partner/focus omit ``raw_query``, so
    both fall through to retrieval-side coreference resolution.
    """
    return bool(req.raw_query) and req.raw_query != req.query


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    req: RetrieveRequest,
    request: Request,
    # SPEC-SEC-SERVICE-AUTH-001 REQ-3: scope check. JWT callers must hold
    # ``klai:internal:retrieval:query``. Internal-secret callers bypass
    # during Phase B/C — see ``require_scope`` docstring.
    _auth: AuthContext = _REQUIRE_RETRIEVAL_SCOPE,
) -> RetrieveResponse:
    # --- Validation ---
    if req.scope in ("personal", "both") and not req.user_id:
        raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")

    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-17 / REQ-6: personal-role callers may
    # only search personal-scope KBs. Force scope to "personal" and strip any
    # caller-supplied kb_slugs so they cannot reach org KBs via this endpoint.
    #
    # Note (SPEC-RAG-PERSONAL-SCOPE-001): the strip below removes the
    # caller-supplied kb_slugs filter, but does NOT widen scope=personal.
    # Server-side canonical narrowing (``_scope_filter`` adds
    # ``kb_slug=personal_kb_slug(user_id)`` for every scope=personal request)
    # re-applies the equivalent filter independent of effective_role.
    if req.effective_role == "personal":
        if req.scope != "personal":
            req = req.model_copy(update={"scope": "personal", "kb_slugs": None})
        elif req.kb_slugs is not None:
            req = req.model_copy(update={"kb_slugs": None})

    # SPEC-RAG-PERSONAL-SCOPE-001 REQ-8: structured observability event.
    # Fires once per request for scope=personal / scope=both so VictoriaLogs
    # can confirm the canonical narrowing is in effect post-deploy. If this
    # event ever stops firing, the canonical filter has regressed silently.
    if req.scope in ("personal", "both") and req.user_id:
        logger.info(
            "retrieval_personal_scope_canonical_filter_applied",
            org_id=req.org_id,
            user_id=req.user_id,
            canonical_slug=personal_kb_slug(req.user_id),
            scope=req.scope,
            effective_role=req.effective_role,
            client_supplied_kb_slugs=req.kb_slugs,
        )

    page_context = req.page_context.model_dump(exclude_none=True) if req.page_context else None

    # SPEC-SEC-010 REQ-3 + SPEC-SEC-IDENTITY-ASSERT-001 REQ-4: cross-user /
    # cross-org guard. JWT callers are matched against their JWT claims;
    # internal-secret callers are re-verified against portal-api so the
    # internal-secret bypass no longer admits arbitrary body identities.
    # On allow this also pins request.state.verified_caller, which is what
    # emit_event below sources for product_events integrity (REQ-6).
    await verify_body_identity(request, req.org_id, req.user_id)

    # SPEC-PRIVACY-QUERY-SHADOW-001 — canonical-level enforcement.
    # The body's ``telemetry_level`` is treated as a *requested upper
    # bound*; the effective level is ``min(client_requested, canonical)``
    # where canonical is portal_orgs.telemetry_level (5-minute cached
    # lookup). This closes the gap where a buggy / malicious caller
    # could send 'full' while the tenant has flipped to 'off', AND
    # makes the knowledge-mcp's hardcoded 'shadow' correct under all
    # tenant configurations (a tenant on 'off' will never see shadow
    # rows from MCP traffic).
    canonical_level = await get_canonical_level(req.org_id)
    effective_level = resolve_effective_level(req.telemetry_level, canonical_level)

    # REQ-13: bind effective_level on the structlog contextvar so the
    # shared anti-leakage processor (in klai-libs/log-utils) sees the
    # right value on EVERY log line in this request — not only the
    # explicit ``decision_record`` event that passes it as a kwarg.
    structlog.contextvars.bind_contextvars(telemetry_level=effective_level)

    t0 = time.perf_counter()
    # @MX:NOTE: [AUTO] Shadow log for parameter tuning (SPEC-KB-021 Change 4).
    # decision_record accumulates timing + decision data throughout the pipeline
    # and is emitted as retrieval_decision_record at the end of the request.
    decision_record: dict = {}

    # 1. Coreference resolution. Skip when the caller already resolved
    # coreference — signalled by a distinct ``raw_query`` (the pre-rewrite
    # original) alongside an already-rewritten ``query``. Re-resolving a
    # caller-rewritten query is a redundant second LLM rewrite that only
    # compounds drift and adds a round-trip. Path A (litellm hook) pre-resolves
    # via klai_kb_query_rewrite; knowledge-mcp sends raw_query==query and
    # partner/focus omit raw_query, so both still resolve here.
    if _caller_pre_resolved(req):
        query_resolved = req.query
        # No coref LLM call ran — do NOT observe the coref latency histogram
        # (a 0.0 sample would skew p50/p95 toward zero). coref_ms stays 0.0 for
        # the decision_record + retrieve log.
        coref_ms = 0.0
        decision_record["coreference_rewrite"] = {
            "original": req.raw_query,
            "resolved": query_resolved,
            "source": "caller",
        }
        decision_record["coreference_ms"] = 0.0
    else:
        t_coref = time.perf_counter()
        query_resolved = await coreference.resolve(req.query, req.conversation_history)
        coref_ms = (time.perf_counter() - t_coref) * 1000
        step_latency_seconds.labels(step="coref").observe(time.perf_counter() - t_coref)
        decision_record["coreference_rewrite"] = {
            "original": req.query,
            "resolved": query_resolved,
            "source": "retrieval-api",
        }
        decision_record["coreference_ms"] = round(coref_ms, 1)

    # 2. Embed resolved query (dense + sparse in parallel). When coreference /
    # query rewrite changed the query, also embed the user's pre-rewrite
    # raw_query so the search can fuse a literal-term RRF leg (see
    # search._search_knowledge). This rescues exact matches — e.g. a product
    # name like "Salesforce" — that an over-eager rewrite would otherwise drop
    # from the candidate pool, where the reranker can no longer recover them.
    t_embed = time.perf_counter()
    raw_query = req.raw_query if req.raw_query and req.raw_query != query_resolved else None
    embed_coros = [embed_single(query_resolved), embed_sparse(query_resolved)]
    if raw_query is not None:
        embed_coros += [embed_single(raw_query), embed_sparse(raw_query)]
    embedded = await asyncio.gather(*embed_coros)
    query_vector, sparse_vector = embedded[0], embedded[1]
    raw_query_vector = embedded[2] if raw_query is not None else None
    raw_sparse_vector = embedded[3] if raw_query is not None else None
    embed_ms = (time.perf_counter() - t_embed) * 1000
    step_latency_seconds.labels(step="embed").observe(time.perf_counter() - t_embed)
    decision_record["embedding_ms"] = round(embed_ms, 1)
    decision_record["raw_query_leg_applied"] = raw_query is not None

    # 3. Gate check. Strict KB mode must never skip retrieval: a wrong bypass
    # would turn "answer only from selected KBs" into a plain model answer.
    # Shadow mode (default) computes + logs the decision but never acts on it.
    if req.kb_narrow:
        gate_decision = gate.GateDecision(
            would_bypass=False,
            bypassed=False,
            margin=None,
            shadow=settings.retrieval_gate_shadow,
        )
        decision_record["gate_skipped_reason"] = "strict_mode"
    else:
        gate_decision = await gate.evaluate(query_vector)

    bypassed = gate_decision.bypassed
    gate_margin = gate_decision.margin
    decision_record["gate_margin"] = round(gate_margin, 4) if gate_margin is not None else None
    decision_record["gate_would_bypass"] = gate_decision.would_bypass
    decision_record["gate_shadow_mode"] = gate_decision.shadow
    decision_record["gate_bypassed"] = bypassed
    decision_record["gate_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    chunks_out: list[ChunkResult] = []
    candidates_retrieved = 0
    reranked_to = 0
    qdrant_ms: float | None = None
    rerank_ms: float | None = None
    graph_results_count = 0
    graph_search_ms: float | None = None
    graph_candidate_ids: set[str] = set()
    graph_in_top_k_ids: list[str] = []
    link_expand_ms: float | None = None
    link_expand_count = 0
    # F3 phase 1 instrumentation (audit retrieval-coupling-2026-05-06):
    # capture seed/expanded sets across the pipeline so the final
    # decision_record can measure link-expansion's contribution to
    # the served top-k. Without this, we cannot tell whether the
    # expanded chunks ever survive the reranker + source-select +
    # quality-boost + evidence-tier passes — i.e. whether the
    # extra Qdrant scroll-call buys anything in practice.
    link_expand_seed_chunk_ids: set[str] = set()
    link_expand_candidate_urls = 0
    # Default-empty serving lists so the bypassed=True path doesn't leave
    # ``serving`` and ``expanded_in_top_k_ids`` unbound. Pyright cannot
    # trace the if-bypassed/else-bypassed mutual exclusion across the
    # two read sites further down (line ~542 + ~555) — initializing
    # here is cleaner than scattered `# type: ignore` comments and makes
    # the bypass path's downstream code defensively correct anyway.
    serving: list[dict] = []
    expanded_in_top_k_ids: list[str] = []

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
            query_vector,
            req,
            settings.retrieval_candidates,
            sparse_vector,
            raw_query_vector=raw_query_vector,
            raw_sparse_vector=raw_sparse_vector,
        )

        graph_task: asyncio.Task[list[dict]] | None = None
        t_graph: float | None = None
        if settings.graphiti_enabled:
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
                    graph_candidate_ids = {r["chunk_id"] for r in graph_results}
                    raw_results = _rrf_merge(raw_results, graph_results)
            except Exception:
                # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
                # traceback that the previous `error=str(exc)` dropped (TRY401).
                logger.warning("Graph search task failed", exc_info=True)

        candidates_retrieved = len(raw_results)
        decision_record["search_candidates_count"] = candidates_retrieved

        # 4b. Link expansion (SPEC-CRAWLER-003 R14-R16)
        if settings.link_expand_enabled and raw_results:
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

            # F3 phase 1: capture seed chunk_ids before expansion so we can
            # measure later how many of the served top-k were original-seed
            # vs newly-expanded vs neither.
            link_expand_seed_chunk_ids = {c["chunk_id"] for c in seed_chunks}
            link_expand_candidate_urls = len(candidate_urls)

            if candidate_urls:
                expansion_chunks = await search.fetch_chunks_by_urls(
                    candidate_urls, req, settings.link_expand_candidates
                )
                existing_ids = {r["chunk_id"] for r in raw_results}
                new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]
                link_expand_count = len(new_chunks)
                # F3 phase 1: tag the expansion chunks. Underscore prefix
                # keeps the field internal — Pydantic ChunkResult ignores
                # unknown fields by default and the build loop only reads
                # explicit keys, so this never leaks to the response body.
                for c in new_chunks:
                    c["_link_expanded"] = True
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

        raw_results, page_context_candidate_boosted = _apply_page_context_boost(
            raw_results,
            page_context,
            mark=False,
        )
        decision_record["page_context_candidates_boosted"] = page_context_candidate_boosted

        # 5. Rerank (skip when reranker disabled)
        if raw_results and settings.reranker_enabled:
            t_rerank = time.perf_counter()
            rerank_input = raw_results[: settings.reranker_candidates]
            rerank_top_n = min(len(rerank_input), max(req.top_k, req.top_k * 3))
            reranked = await reranker.rerank(query_resolved, rerank_input, rerank_top_n)
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

        # 5a-ter. SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-3 — link-expand
        # reranker boost. Applied AFTER rerank and BEFORE quality-floor so
        # expanded chunks get a fair shot at surviving source-aware-select.
        # Default boost=1.00 is a no-op until operator tunes the env var.
        reranked = _apply_link_expand_boost(
            reranked,
            boost=settings.link_expand_score_boost,
            enabled=settings.link_expand_enabled,
        )
        if settings.reranker_enabled:
            reranked, page_context_boosted = _apply_page_context_boost(
                reranked,
                page_context,
            )
        else:
            page_context_boosted = page_context_candidate_boosted
        decision_record["page_context_boosted"] = page_context_boosted

        # 5a-bis. Quality-floor filter (SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07).
        # Removes chunks explicitly degraded to quality_score=0.0 BEFORE the
        # source-quota algorithm picks candidates — otherwise a walled chunk
        # could burn a diversity slot. The default floor (0.05) cannot
        # accidentally filter neutral 0.5 chunks; an operator must set the
        # threshold > 0.5 explicitly.
        reranked, quality_floor_filtered = filter_quality_floor(
            reranked, floor=settings.retrieval_quality_floor
        )
        decision_record["quality_floor_filtered"] = quality_floor_filtered
        if quality_floor_filtered > 0:
            # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-08 — labelled by org_id so
            # per-tenant pollution is visible in Grafana. Only increment on
            # non-zero to keep metric cardinality predictable for tenants
            # whose chunks never trip the floor.
            quality_floor_filtered_total.labels(org_id=req.org_id).inc(quality_floor_filtered)

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

        # F3 phase 1 instrumentation: emit link-expansion contribution to
        # the served top-k. Lets us answer "did the extra Qdrant scroll
        # ever produce a chunk that beat the reranker top-k cut-off?"
        # before deciding on phase 2 (RRF migration vs disable).
        # Audit ref: .moai/audits/retrieval-coupling-2026-05-06/findings/
        # F3-link-expansion-dead-weight.md
        expanded_in_top_k_ids = [r["chunk_id"] for r in serving if r.get("_link_expanded")]
        seed_in_top_k_ids = [
            r["chunk_id"] for r in serving if r["chunk_id"] in link_expand_seed_chunk_ids
        ]
        decision_record["link_expand"] = {
            "enabled": settings.link_expand_enabled,
            "seed_k": len(link_expand_seed_chunk_ids),
            "candidate_urls": link_expand_candidate_urls,
            "expanded_added": link_expand_count,
            "expanded_in_top_k": len(expanded_in_top_k_ids),
            "expanded_top_k_chunk_ids": expanded_in_top_k_ids,
            "seed_in_top_k": len(seed_in_top_k_ids),
            "served_top_k": len(serving),
        }

        # Issue #71 measurement gate: before adding any custom graph traversal,
        # prove whether the current Graphiti graph leg contributes to served
        # top-K at all. This is intentionally observability-only.
        graph_in_top_k_ids = [
            r["chunk_id"] for r in serving if r["chunk_id"] in graph_candidate_ids
        ]
        decision_record["graph_search"] = {
            "enabled": settings.graphiti_enabled,
            "candidates_returned": graph_results_count,
            "graph_in_top_k": len(graph_in_top_k_ids),
            "graph_top_k_chunk_ids": graph_in_top_k_ids,
            "served_top_k": len(serving),
        }

        # 6b. SPEC-RAG-PARENT-CHILD-001: swap child text for the parent's
        # broader-context text. Fetched in one batch query against
        # knowledge.parent_chunks. Children with no parent_chunk_id (legacy
        # ingests) keep their own text — REQ-3 fall-through.
        from retrieval_api.services import parent_lookup

        parent_id_per_serving: list[int | None] = [r.get("parent_chunk_id") for r in serving]
        parent_text_by_id = await parent_lookup.fetch_parents(
            pid for pid in parent_id_per_serving if pid is not None
        )

        # 7. Build ChunkResult objects (with parent-text swap when available)
        chunks_out = []
        for r in serving:
            pid = r.get("parent_chunk_id")
            if pid is not None and pid in parent_text_by_id:
                display_text = parent_text_by_id[pid]
                is_parent = True
            else:
                display_text = r["text"]
                is_parent = False
            chunks_out.append(
                ChunkResult(
                    chunk_id=r["chunk_id"],
                    artifact_id=r.get("artifact_id"),
                    content_type=r.get("content_type"),
                    text=display_text,
                    context_prefix=r.get("context_prefix"),
                    heading_path=r.get("heading_path"),
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
                    original_filename=r.get("original_filename"),
                    image_urls=payload_list(r, "image_urls") or None,
                    entity_names=payload_list(r, "entity_names") or None,
                    is_parent_text=is_parent,
                )
            )

    retrieval_ms = (time.perf_counter() - t0) * 1000
    step_latency_seconds.labels(step="total").observe(retrieval_ms / 1000)
    retrieval_requests_total.labels(scope=req.scope, bypassed=str(bypassed).lower()).inc()
    retrieval_chunks_total.labels(scope=req.scope).observe(len(chunks_out))

    # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 — confidence band on the
    # served chunks. Bypass paths (gate) get None; retrieval paths always
    # report a band so downstream consumers (litellm-hook REQ-2) can decide.
    if bypassed:
        confidence_band: ConfidenceBand | None = None
    else:
        confidence_band = _compute_confidence_band(
            serving,
            high_threshold=settings.confidence_band_high_threshold,
            low_threshold=settings.confidence_band_low_threshold,
            reranker_enabled=settings.reranker_enabled,
        )
        decision_record["confidence_band"] = confidence_band
        retrieval_confidence_band_total.labels(band=confidence_band, org_id=req.org_id).inc()

    evidence_query = (
        f"{req.raw_query}\n{query_resolved}"
        if req.raw_query and req.raw_query != query_resolved
        else query_resolved
    )
    evidence_pack = build_evidence_pack(
        chunks_out,
        query=evidence_query,
    )
    decision_record["evidence_pack"] = {
        "item_count": len(evidence_pack.items),
        "source_count": len(evidence_pack.sources),
        "no_citable_reason": evidence_pack.no_citable_reason,
        "top_source_labels": [
            source.source_label or source.title for source in evidence_pack.sources[:3]
        ],
        "sources": _evidence_pack_decision_sources(evidence_pack),
        "top_item_chunk_ids": [item.chunk_id for item in evidence_pack.items[:5]],
    }

    # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-8 — link-expand survival
    # outcome counter. Only count when link-expand actually contributed
    # candidates (link_expand_count > 0). Hit = at least one expanded chunk
    # survived to the served top-K; miss = none did.
    if not bypassed and link_expand_count > 0:
        outcome = "hit" if expanded_in_top_k_ids else "miss"
        retrieval_link_expand_top_k_total.labels(outcome=outcome, org_id=req.org_id).inc()

    # Issue #71: only count requests where Graphiti returned candidates. Empty
    # graph searches are measured by graph_results_count and should not dilute
    # the contribution ratio.
    if not bypassed and graph_results_count > 0:
        outcome = "hit" if graph_in_top_k_ids else "miss"
        retrieval_graph_top_k_total.labels(outcome=outcome, org_id=req.org_id).inc()

    decision_record["total_ms"] = round(retrieval_ms, 1)

    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-5: gate raw-query content on
    # decision_record. In off + shadow mode, strip the coreference
    # rewrite text before serialization. The defense-in-depth structlog
    # processor (REQ-13) catches the same fields if they slip through
    # via a future code path that bypasses this check, but doing it
    # here keeps the metric-level gating accurate.
    #
    # REQ-10: retention_class is a structured label so Alloy can route
    # 'content' events to a 7d-retention stream and 'metadata' events
    # to the existing 30d stream (operator-side VictoriaLogs config —
    # follow-up runbook in Unit 8).
    if effective_level != "full" and "coreference_rewrite" in decision_record:
        decision_record.pop("coreference_rewrite", None)
        decision_record["retention_class"] = "metadata"
        telemetry_level_decisions_total.labels(
            level=effective_level, decision="metadata_only"
        ).inc()
    elif effective_level == "full":
        decision_record["retention_class"] = "content"
        telemetry_level_decisions_total.labels(level="full", decision="content_emitted").inc()
    else:
        # off mode without coreference_rewrite already in the record.
        decision_record["retention_class"] = "metadata"

    try:
        logger.info(
            "retrieval_decision_record",
            org_id=req.org_id,
            scope=req.scope,
            telemetry_level=effective_level,
            **decision_record,
        )
    except Exception:
        logger.exception("decision_record_emit_failed")

    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-7: shadow-store INSERT for shadow
    # + full modes. Fire-and-forget — failures are counted in
    # telemetry_shadow_drop_total, not propagated. ``request_id`` is
    # bound by RequestContextMiddleware (logging_setup.py); read back
    # from structlog contextvars so we don't have to plumb it through
    # the entire pipeline. If the contextvar is missing (e.g. middleware
    # didn't run, or upstream dropped a malformed X-Request-ID), generate
    # a server-side UUID4 so every retrieve call still produces a row —
    # missing rows would silently degrade observability and bias the
    # dashboards' decision counters.
    if effective_level in ("shadow", "full"):
        request_id_for_shadow = structlog.contextvars.get_contextvars().get("request_id") or str(
            uuid.uuid4()
        )
        chunk_ids_for_shadow = [c.chunk_id for c in chunks_out]
        reranker_scores = [c.reranker_score for c in chunks_out if c.reranker_score is not None]
        reranker_top1 = max(reranker_scores) if reranker_scores else None
        features_dict = extract_features(req.query)
        write_shadow(
            request_id=request_id_for_shadow,
            org_id=req.org_id,
            embedding=list(query_vector) if query_vector is not None else None,
            features=features_dict,
            band=confidence_band,
            chunk_ids=chunk_ids_for_shadow,
            reranker_top1=reranker_top1,
        )
        telemetry_level_decisions_total.labels(
            level=effective_level, decision="shadow_inserted"
        ).inc()
        if effective_level == "full":
            telemetry_level_decisions_total.labels(level="full", decision="full_logged").inc()

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

    # SPEC-GRAFANA-METRICS: knowledge.queried event.
    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-6: tenant_id / user_id MUST come from
    # the verified identity pin set by verify_body_identity, never from the
    # request body. Body fields are caller-supplied; product_events is a
    # business-metrics contract whose integrity we cannot let any caller
    # poison. Tenant-only service calls deliberately emit user_id=None.
    verified = getattr(request.state, "verified_caller", None)
    verified_tenant = getattr(request.state, "verified_tenant", None)
    if verified is not None:
        event_tenant_id = verified.org_id
        event_user_id = verified.user_id
    elif verified_tenant is not None:
        event_tenant_id = verified_tenant.org_id
        event_user_id = None
    else:
        event_tenant_id = None
        event_user_id = None

    if event_tenant_id is not None:
        emit_event(
            "knowledge.queried",
            tenant_id=event_tenant_id,
            user_id=event_user_id,
            properties={
                "scope": req.scope,
                "kb_slugs": list(req.kb_slugs) if req.kb_slugs else [],
                "had_results": len(chunks_out) > 0,
                "result_count": len(chunks_out),
            },
        )
    else:
        # Defense in depth: the auth middleware should always populate
        # request.state.auth, and verify_body_identity should pin either a
        # verified user or verified tenant on the authenticated success path.
        # If we see this log line in production, a new route is bypassing the
        # guard or auth state was not installed.
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
        confidence_band=confidence_band,
        evidence_pack=evidence_pack,
    )
