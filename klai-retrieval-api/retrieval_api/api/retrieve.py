"""POST /retrieve endpoint -- structured retrieval pipeline."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from klai_kb_slugs import personal_kb_slug
from qdrant_client.models import SparseVector

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
    EvidencePack,
    RetrieveMetadata,
    RetrieveRequest,
    RetrieveResponse,
    SubQueryResult,
)
from retrieval_api.quality_boost import quality_boost
from retrieval_api.quality_floor import filter_quality_floor
from retrieval_api.services import coreference, graph_search, reranker, search
from retrieval_api.services.diversity import source_aware_select
from retrieval_api.services.events import emit_event
from retrieval_api.services.evidence_pack import (
    DEFAULT_MAX_SOURCES,
    MULTI_KB_MAX_SOURCES,
    build_evidence_pack,
    chunk_source_key,
    merge_evidence_packs,
)
from retrieval_api.services.features import extract_features
from retrieval_api.services.router import fetch_source_catalog, route_to_sources
from retrieval_api.services.tei import embed_single, embed_sparse
from retrieval_api.services.telemetry import write_shadow
from retrieval_api.services.tenant_telemetry import get_canonical_level, resolve_effective_level
from retrieval_api.tracing import RetrievalTrace
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


def _set_final_rank_scores(chunks: list[dict]) -> None:
    """Stamp the single post-rerank ranking truth (REQ-RANK-01).

    Called ONLY when the ranking contract is active — and on the shadow
    preview copies. In shadow mode the serving chunks deliberately carry NO
    ``final_rank_score``, so every downstream sort falls back to its
    pre-contract key and serving stays byte-identical (REQ-RANK-04).
    """
    for chunk in chunks:
        if isinstance(chunk.get("reranker_score"), (int, float)):
            chunk["final_rank_score"] = chunk["reranker_score"]
        else:
            chunk["final_rank_score"] = chunk.get("score", 0.0)


# Fields the shadow preview needs to replay the post-rerank pipeline
# (boosts, quality-floor, source-aware-select, quality-boost) plus the
# snapshot projection. Chunk text and enrichment payloads stay out — the
# preview exists for ordering comparison only.
_SHADOW_PREVIEW_KEYS = (
    "chunk_id",
    "source_url",
    "artifact_id",
    "source_label",
    "kb_slug",
    "reranker_score",
    "score",
    "quality_score",
    "feedback_count",
    "incoming_link_count",
    "_link_expanded",
)


def _ranking_contract_snapshot(
    chunks: list[dict], *, max_sources: int = DEFAULT_MAX_SOURCES
) -> dict[str, list]:
    """Project a serving list onto the shadow-comparison shape (REQ-RANK-04).

    ``evidence_source_keys`` mirrors ``build_evidence_pack``'s source-slot
    assignment exactly (first N DISTINCT source keys, URL-or-artifact) via
    the shared ``chunk_source_key`` helper — first-N raw source_urls would
    double-count multi-chunk sources and miss URL-less uploads.
    """
    source_keys: list[str] = []
    for chunk in chunks:
        key = chunk_source_key(chunk)
        if key and key not in source_keys:
            source_keys.append(key)
        if len(source_keys) >= max_sources:
            break
    return {
        "top5_chunk_ids": [chunk.get("chunk_id") for chunk in chunks[:5]],
        "evidence_source_keys": source_keys,
    }


def _caller_pre_resolved(req: RetrieveRequest) -> bool:
    """Whether the caller already resolved coreference for this request.

    Preferred signal: the explicit ``coreference_resolved`` flag. The litellm
    hook sets it True whenever its rewrite step made a decision — including
    the destructive-rewrite guard discarding a bad rewrite, in which case
    ``raw_query == query`` on purpose. Re-resolving that shape here would rerun
    the exact history-hijack the guard just blocked.

    Legacy fallback (flag absent): a non-empty ``raw_query`` that differs from
    ``query`` — older litellm hook builds. knowledge-mcp sends
    ``raw_query == query`` (no rewrite) and partner/focus omit ``raw_query``,
    so both fall through to retrieval-side coreference resolution.
    """
    if req.coreference_resolved is not None:
        return req.coreference_resolved
    return bool(req.raw_query) and req.raw_query != req.query


_BAND_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}


async def _retrieve_sub_queries(
    req: RetrieveRequest,
    request: Request,
    auth: AuthContext,
) -> RetrieveResponse:
    """Fan out one full retrieval per sub-question and merge the results.

    Each sub-question runs the complete single-query pipeline (embedding,
    search, rerank, evidence pack) via a recursive ``retrieve`` call,
    so per-sub-question decision records, metrics, and identity verification
    all behave exactly like a normal request. The merge namespaces evidence
    ids per question (``merge_evidence_packs``) and reports per-question
    coverage in ``sub_results`` — a failed sub-question is reported as an
    error, never conflated with "not in the knowledge base".
    """
    sub_queries = [
        (index, query.strip())
        for index, query in enumerate(req.sub_queries or [], 1)
        if isinstance(query, str) and query.strip()
    ]
    t0 = time.perf_counter()
    per_query_top_k = max(1, min(req.top_k, settings.sub_query_top_k))

    # Bounds how many sub-question pipelines run concurrently WITHIN this
    # one request's fan-out — created fresh per call so different users'
    # requests never contend with each other, only the sub-questions of the
    # SAME message do. See Settings.sub_query_max_concurrent for why: the
    # reranker is one shared GPU instance and an unbounded gather() over up
    # to MAX_SUB_QUESTIONS (6) sub-questions would burst it with that many
    # full pipelines (embed + qdrant + graphiti + rerank) at once.
    semaphore = asyncio.Semaphore(settings.sub_query_max_concurrent)

    async def _one(sub_query: str) -> RetrieveResponse:
        sub_req = req.model_copy(
            update={
                "query": sub_query,
                # Sub-questions are standalone TEXT (splitting never rewrites
                # them), but a question like "En hoe lang duurt het?" still
                # needs coreference resolution against conversation_history
                # (already carried on ``req`` and preserved by model_copy)
                # to resolve "het". ``coreference_resolved: None`` lets
                # retrieval-api's own coreference step run per sub-question
                # instead of treating the raw text as already-resolved.
                # ``raw_query`` is set to the pre-coreference sub-question
                # text itself (not None): the existing logic in
                # ``retrieve()`` only activates the extra literal-term RRF
                # leg when ``raw_query != query_resolved`` — so this is a
                # no-op when this sub-question's own coreference step makes
                # no change (same behaviour as before), but if it DOES
                # rewrite, the raw leg can still recover exact terms the
                # rewrite dropped.
                "raw_query": sub_query,
                "coreference_resolved": None,
                "sub_queries": None,
                "top_k": per_query_top_k,
            }
        )
        async with semaphore:
            return await retrieve(sub_req, request, _auth=auth)

    # Each recursive ``retrieve()`` call below would otherwise emit its own
    # knowledge.queried product event — 6 events for one user question. Mark
    # the request as an internal sub-query call so the emit-site in
    # ``retrieve()`` skips it; this function emits a single event for the
    # original question after the fan-out completes (see below).
    request.state.klai_sub_query_internal = True
    try:
        results = await asyncio.gather(
            *[_one(query) for _, query in sub_queries], return_exceptions=True
        )
    finally:
        request.state.klai_sub_query_internal = False

    sub_results: list[SubQueryResult] = []
    indexed_packs: list[tuple[int, EvidencePack | None]] = []
    merged_chunks: list[ChunkResult] = []
    seen_chunk_ids: set[str] = set()
    candidates_total = 0
    reranked_total = 0
    failures = 0
    for (index, query), result in zip(sub_queries, results, strict=True):
        if isinstance(result, BaseException):
            # A 4xx from a sub-call is an auth/validation failure of the
            # WHOLE request (bad identity, bad scope), not a per-question
            # retrieval miss. Re-raise it as-is so the caller sees the real
            # status code instead of a misleading 502 once every sub-query
            # is (wrongly) treated as failed.
            if isinstance(result, HTTPException) and result.status_code < 500:
                raise result
            failures += 1
            logger.warning(
                "retrieval_sub_query_failed",
                org_id=req.org_id,
                sub_query_index=index,
                error=type(result).__name__,
            )
            sub_results.append(
                SubQueryResult(index=index, query=query, error=type(result).__name__)
            )
            continue
        pack = result.evidence_pack
        evidence_count = len(pack.items) if pack is not None else 0
        indexed_packs.append((index, pack))
        sub_results.append(
            SubQueryResult(
                index=index,
                query=query,
                confidence_band=result.confidence_band,
                evidence_count=evidence_count,
            )
        )
        candidates_total += result.metadata.candidates_retrieved
        reranked_total += result.metadata.reranked_to
        for chunk in result.chunks:
            if chunk.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk.chunk_id)
            merged_chunks.append(chunk)

    if failures == len(sub_queries):
        raise HTTPException(status_code=502, detail="all sub-query retrievals failed")

    covered_bands = [sub.confidence_band for sub in sub_results if sub.confidence_band is not None]
    overall_band = (
        max(covered_bands, key=lambda band: _BAND_RANK.get(band, 0)) if covered_bands else "unknown"
    )
    merged_pack = merge_evidence_packs(indexed_packs)
    retrieval_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "retrieval_sub_query_fanout",
        org_id=req.org_id,
        sub_query_count=len(sub_queries),
        failures=failures,
        evidence_items=len(merged_pack.items),
        retrieval_ms=round(retrieval_ms, 1),
    )

    # SPEC-GRAFANA-METRICS: knowledge.queried event for the ORIGINAL question
    # — the per-sub-question emits were suppressed above. Mirrors the emit
    # site in ``retrieve()`` exactly: same identity precedence, same
    # properties shape.
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
                "had_results": len(merged_chunks) > 0,
                "result_count": len(merged_chunks),
            },
        )
    else:
        logger.warning(
            "product_event_skipped_no_identity",
            event_type="knowledge.queried",
            scope=req.scope,
            path=request.url.path,
        )

    return RetrieveResponse(
        query_resolved=req.query,
        retrieval_bypassed=False,
        chunks=merged_chunks,
        metadata=RetrieveMetadata(
            candidates_retrieved=candidates_total,
            reranked_to=reranked_total,
            retrieval_ms=round(retrieval_ms, 1),
        ),
        confidence_band=overall_band,
        evidence_pack=merged_pack,
        sub_results=sub_results,
    )


async def _label_graph_results(graph_results: list[dict], req: RetrieveRequest) -> None:
    """Attach the citation fields a graph edge cannot know about itself.

    A graph edge carries a fact and a pointer to the document it came from,
    but no title or URL, so ``evidence_pack._title()`` would fall through to
    ``text[:80]`` and render a truncated fact as the source name.

    Two pointer schemes coexist and both are resolved here:

    - ``(kb_slug, path)`` — SPEC-RAG-GRAPH-CITE-002, stable across re-ingest.
      Also yields the CURRENT artifact_id, which is what makes the edge
      citable at all.
    - a legacy ``artifact_id`` — every edge written before that change. It
      identifies a version, so it only resolves while that version is still
      the current one; those edges heal on their document's next ingest.

    Setting ``source_url`` also collapses a graph fact and an ordinary chunk
    from the same document into ONE source, because ``chunk_source_key`` keys
    on the URL before falling back to ``artifact:<id>``.

    Mutates in place. Fail-open: an unresolved edge keeps whatever identity it
    already had, so a failed lookup never costs a result.
    """
    documents = [
        (r["graph_kb_slug"], r["graph_path"])
        for r in graph_results
        if r.get("graph_kb_slug") and r.get("graph_path")
    ]
    artifact_ids = [r.get("artifact_id") for r in graph_results if r.get("artifact_id")]
    if not documents and not artifact_ids:
        return

    by_document, by_artifact = await asyncio.gather(
        search.fetch_document_display_metadata(documents, req),
        search.fetch_artifact_display_metadata(artifact_ids, req),
    )

    for result in graph_results:
        key = (result.get("graph_kb_slug"), result.get("graph_path"))
        meta = (
            by_document.get(key) if all(key) else by_artifact.get(result.get("artifact_id") or "")
        )
        if not meta:
            continue
        for field in ("title", "source_url", "source_label", "original_filename"):
            if meta.get(field):
                result[field] = meta[field]
        if meta.get("artifact_id"):
            result["artifact_id"] = meta["artifact_id"]


@dataclass
class RetrievalPipelineState:
    """Mutable values shared by the ordered retrieval pipeline stages."""

    req: RetrieveRequest
    request: Request
    evidence_max_sources: int
    page_context: dict[str, Any] | None
    effective_level: str
    t0: float
    trace: RetrievalTrace
    decision_record: dict[str, Any] = dataclass_field(default_factory=dict)
    ranking_contract_mode: str = ""
    query_resolved: str = ""
    coref_ms: float = 0.0
    query_vector: list[float] | None = None
    sparse_vector: SparseVector | None = None
    raw_query_vector: list[float] | None = None
    raw_sparse_vector: SparseVector | None = None
    embed_ms: float = 0.0
    router_selected: set[str] | None = None
    raw_results: list[dict[str, Any]] = dataclass_field(default_factory=list)
    candidates_retrieved: int = 0
    qdrant_ms: float | None = None
    graph_results_count: int = 0
    graph_search_ms: float | None = None
    graph_candidate_ids: set[str] = dataclass_field(default_factory=set)
    link_expand_ms: float | None = None
    link_expand_count: int = 0
    link_expand_seed_chunk_ids: set[str] = dataclass_field(default_factory=set)
    link_expand_candidate_urls: int = 0
    page_context_candidate_boosted: int = 0
    reranked: list[dict[str, Any]] = dataclass_field(default_factory=list)
    reranked_to: int = 0
    rerank_ms: float | None = None
    ranking_shadow_preview: list[dict[str, Any]] | None = None
    excluded_preferred_kb_slugs: set[str] | None = None
    serving: list[dict[str, Any]] = dataclass_field(default_factory=list)
    expanded_in_top_k_ids: list[str] = dataclass_field(default_factory=list)
    graph_in_top_k_ids: list[str] = dataclass_field(default_factory=list)
    parent_text_by_id: dict[int, str] = dataclass_field(default_factory=dict)
    chunks_out: list[ChunkResult] = dataclass_field(default_factory=list)
    retrieval_ms: float = 0.0
    confidence_band: ConfidenceBand = "unknown"
    evidence_pack: EvidencePack | None = None


async def _resolve_identity_and_telemetry(
    req: RetrieveRequest,
    request: Request,
    auth: AuthContext,
) -> RetrievalPipelineState | RetrieveResponse:
    """Validate identity and resolve the request's telemetry contract."""

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

    evidence_max_sources = (
        MULTI_KB_MAX_SOURCES if len(set(req.kb_slugs or ())) > 1 else DEFAULT_MAX_SOURCES
    )

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

    # Multi-part fan-out: >= 2 usable sub-questions delegate to the merge
    # path; each sub-question re-enters this endpoint as a normal request.
    # Deliberately placed AFTER verify_body_identity: an identity mismatch
    # (403) must surface immediately, not get relabeled as a 502
    # "all sub-query retrievals failed" once it reaches every sub-call. Uses
    # the already-narrowed ``req`` (personal-role scope/kb_slugs stripping
    # above already applied).
    if req.sub_queries and sum(1 for q in req.sub_queries if isinstance(q, str) and q.strip()) >= 2:
        return await _retrieve_sub_queries(req, request, auth)

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
    request_id = structlog.contextvars.get_contextvars().get("request_id") or str(uuid.uuid4())
    trace = RetrievalTrace(
        request_id=request_id,
        org_id=req.org_id,
        scope=req.scope,
        telemetry_level=effective_level,
        started_at=t0,
    )
    # Gate and evidence-tier experiments were retired in August 2026. Keep
    # their flat compatibility fields truthful without restoring either
    # behavior: retrieval never bypasses and evidence shadow mode is inactive.
    trace.meta("gate_margin", None)
    trace.meta("gate_bypassed", False)
    trace.meta("gate_ms", 0.0)
    trace.meta("evidence_shadow_mode", False)
    # @MX:NOTE: [AUTO] Shadow log for parameter tuning (SPEC-KB-021 Change 4).
    # decision_record accumulates timing + decision data throughout the pipeline
    # and is emitted as retrieval_decision_record at the end of the request.
    decision_record: dict[str, Any] = {}
    # SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK-04 — validated pydantic
    # setting (config.py), not a raw env read: invalid values fail at boot.
    ranking_contract_mode = settings.ranking_contract_mode
    decision_record["ranking_contract_mode"] = ranking_contract_mode

    return RetrievalPipelineState(
        req=req,
        request=request,
        evidence_max_sources=evidence_max_sources,
        page_context=page_context,
        effective_level=effective_level,
        t0=t0,
        trace=trace,
        decision_record=decision_record,
        ranking_contract_mode=ranking_contract_mode,
    )


async def _run_coreference(state: RetrievalPipelineState) -> None:
    """Resolve coreference unless the caller already did so."""

    req = state.req
    trace = state.trace
    # 1. Coreference resolution. Skip when the caller already resolved
    # coreference — signalled by a distinct ``raw_query`` (the pre-rewrite
    # original) alongside an already-rewritten ``query``. Re-resolving a
    # caller-rewritten query is a redundant second LLM rewrite that only
    # compounds drift and adds a round-trip. Path A (litellm hook) pre-resolves
    # via klai_kb_query_rewrite; knowledge-mcp sends raw_query==query and
    # partner/focus omit raw_query, so both still resolve here.
    if _caller_pre_resolved(req):
        state.query_resolved = req.query
        # No coref LLM call ran — do NOT observe the coref latency histogram
        # (a 0.0 sample would skew p50/p95 toward zero). coref_ms stays 0.0 for
        # the decision_record + retrieve log.
        state.coref_ms = 0.0
        trace.content(
            "coreference_rewrite",
            {
                "original": req.raw_query,
                "resolved": state.query_resolved,
                "source": "caller",
            },
        )
        trace.meta("coreference_ms", 0.0)
        trace.record_ok("coreference", 0.0, source="caller")
    else:
        t_coref = time.perf_counter()
        async with trace.step("coreference", started_at=t_coref) as coreference_step:
            state.query_resolved = await coreference.resolve(
                req.query, req.conversation_history, telemetry_level=state.effective_level
            )
            coreference_step.meta("source", "retrieval-api")
        state.coref_ms = coreference_step.duration_ms
        step_latency_seconds.labels(step="coref").observe(state.coref_ms / 1000)
        trace.content(
            "coreference_rewrite",
            {
                "original": req.query,
                "resolved": state.query_resolved,
                "source": "retrieval-api",
            },
        )
        trace.meta("coreference_ms", round(state.coref_ms, 1))


async def _run_embed(state: RetrievalPipelineState) -> None:
    """Embed the resolved query and optional literal raw-query leg."""

    req = state.req
    # 2. Embed resolved query (dense + sparse in parallel). When coreference /
    # query rewrite changed the query, also embed the user's pre-rewrite
    # raw_query so the search can fuse a literal-term RRF leg (see
    # search._search_knowledge). This rescues exact matches — e.g. a product
    # name like "Salesforce" — that an over-eager rewrite would otherwise drop
    # from the candidate pool, where the reranker can no longer recover them.
    t_embed = time.perf_counter()
    async with state.trace.step("embed", started_at=t_embed) as embed_step:
        raw_query = (
            req.raw_query if req.raw_query and req.raw_query != state.query_resolved else None
        )
        embed_coros = [embed_single(state.query_resolved), embed_sparse(state.query_resolved)]
        if raw_query is not None:
            embed_coros += [embed_single(raw_query), embed_sparse(raw_query)]
        embedded = await asyncio.gather(*embed_coros)
        state.query_vector = cast(list[float], embedded[0])
        state.sparse_vector = cast(SparseVector | None, embedded[1])
        state.raw_query_vector = cast(list[float], embedded[2]) if raw_query is not None else None
        state.raw_sparse_vector = (
            cast(SparseVector | None, embedded[3]) if raw_query is not None else None
        )
        embed_step.meta("raw_query_leg_applied", raw_query is not None)
    state.embed_ms = embed_step.duration_ms
    step_latency_seconds.labels(step="embed").observe(state.embed_ms / 1000)
    state.trace.meta("embedding_ms", round(state.embed_ms, 1))
    state.decision_record["raw_query_leg_applied"] = raw_query is not None


async def _run_router(state: RetrievalPipelineState) -> None:
    """Identify relevant sources for post-rerank selection."""

    req = state.req
    # 3b. Query router — identifies relevant sources for post-rerank selection
    router_meta: dict[str, Any] = {
        "router_decision": None,
        "router_layer_used": "skipped",
    }
    async with state.trace.step("router") as router_step:
        if not settings.router_enabled:
            router_step.skip("disabled_by_config")
        elif req.scope not in ("org", "both"):
            router_step.skip("scope_not_applicable")
        else:
            source_label_catalog = await fetch_source_catalog(req.org_id, req.kb_slugs)
            if len(source_label_catalog) < settings.router_min_source_label_count:
                router_step.skip(
                    "insufficient_source_labels",
                    source_label_count=len(source_label_catalog),
                )
            else:
                routing = await route_to_sources(
                    query_resolved=state.query_resolved,
                    query_vector=state.query_vector,
                    org_id=req.org_id,
                    source_label_catalog=source_label_catalog,
                    margin_single=settings.router_margin_single,
                    margin_dual=settings.router_margin_dual,
                    llm_fallback=settings.router_llm_fallback,
                    centroid_ttl_seconds=settings.router_centroid_ttl_seconds,
                    kb_slugs=req.kb_slugs,
                )
                if routing.selected_source_labels:
                    state.router_selected = set(routing.selected_source_labels)
                router_meta = {
                    "router_decision": routing.selected_source_labels,
                    "router_layer_used": routing.layer_used,
                    "router_margin": routing.margin,
                    "router_centroid_cache_hit": routing.cache_hit,
                }
        router_step.meta("router_layer_used", router_meta["router_layer_used"])
    state.trace.meta("router", router_meta)


async def _run_search(state: RetrievalPipelineState) -> None:
    """Run Qdrant and optional Graphiti search with their existing semantics."""

    req = state.req
    # 4. Search — Qdrant + Graphiti in parallel (AC-5)
    t_qdrant = time.perf_counter()
    qdrant_coro = search.hybrid_search(
        state.query_vector,
        req,
        settings.retrieval_candidates,
        state.sparse_vector,
        raw_query_vector=state.raw_query_vector,
        raw_sparse_vector=state.raw_sparse_vector,
    )

    graph_task: asyncio.Task[list[dict]] | None = None
    t_graph: float | None = None
    if settings.graphiti_enabled:
        t_graph = time.perf_counter()
        graph_task = asyncio.create_task(
            graph_search.search(state.query_resolved, req.org_id, top_k=20)
        )

    async with state.trace.step("qdrant_search", started_at=t_qdrant) as qdrant_step:
        state.raw_results = await qdrant_coro
        qdrant_step.meta("candidates_returned", len(state.raw_results))
    state.qdrant_ms = qdrant_step.duration_ms
    step_latency_seconds.labels(step="qdrant").observe(state.qdrant_ms / 1000)
    state.decision_record["search_ms"] = round(state.qdrant_ms, 1)

    if graph_task is not None and t_graph is not None:
        try:
            async with state.trace.step("graph_search", started_at=t_graph) as graph_step:
                graph_results = await graph_task
                state.graph_results_count = len(graph_results)
                graph_step.meta("candidates_returned", state.graph_results_count)
                if graph_results:
                    await _label_graph_results(graph_results, req)
                    state.graph_candidate_ids = {r["chunk_id"] for r in graph_results}
                    state.raw_results = _rrf_merge(state.raw_results, graph_results)
            state.graph_search_ms = graph_step.duration_ms
            step_latency_seconds.labels(step="graph").observe(state.graph_search_ms / 1000)
        except Exception:
            # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
            # traceback that the previous `error=str(exc)` dropped (TRY401).
            logger.warning("Graph search task failed", exc_info=True)
    else:
        state.trace.mark_skipped("graph_search", "disabled_by_config")

    state.candidates_retrieved = len(state.raw_results)
    state.decision_record["search_candidates_count"] = state.candidates_retrieved


async def _run_link_expand(state: RetrievalPipelineState) -> None:
    """Expand linked candidates and apply adjacent candidate boosts."""

    req = state.req
    # 4b. Link expansion (SPEC-CRAWLER-003 R14-R16)
    if not settings.link_expand_enabled:
        link_expand_step = state.trace.mark_skipped("link_expand", "disabled_by_config")
    elif not state.raw_results:
        link_expand_step = state.trace.mark_skipped("link_expand", "no_candidates")
    else:
        t_expand = time.perf_counter()
        async with state.trace.step("link_expand", started_at=t_expand) as link_expand_step:
            seed_chunks = state.raw_results[: settings.link_expand_seed_k]
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
            state.link_expand_seed_chunk_ids = {c["chunk_id"] for c in seed_chunks}
            state.link_expand_candidate_urls = len(candidate_urls)
            link_expand_step.meta("seed_k", len(seed_chunks))
            link_expand_step.meta("candidate_urls", state.link_expand_candidate_urls)

            if not candidate_urls:
                link_expand_step.skip("no_candidate_urls")
            else:
                expansion_chunks = await search.fetch_chunks_by_urls(
                    candidate_urls, req, settings.link_expand_candidates
                )
                existing_ids = {r["chunk_id"] for r in state.raw_results}
                new_chunks = [c for c in expansion_chunks if c["chunk_id"] not in existing_ids]
                state.link_expand_count = len(new_chunks)
                link_expand_step.meta("expanded_added", state.link_expand_count)
                # F3 phase 1: tag the expansion chunks. Underscore prefix
                # keeps the field internal — Pydantic ChunkResult ignores
                # unknown fields by default and the build loop only reads
                # explicit keys, so this never leaks to the response body.
                for c in new_chunks:
                    c["_link_expanded"] = True
                state.raw_results = state.raw_results + new_chunks

        state.link_expand_ms = link_expand_step.duration_ms
        step_latency_seconds.labels(step="link_expand").observe(state.link_expand_ms / 1000)
        logger.debug(
            "link_expand",
            seed_k=len(seed_chunks),
            candidate_urls=len(candidate_urls),
            new_chunks=state.link_expand_count,
        )

    # 4c. Authority boost (SPEC-CRAWLER-003 R17), retained only for
    # ranking-contract shadow comparison. Active mode serves by the
    # post-rerank final_rank_score contract instead.
    authority_boost_enabled = (
        state.ranking_contract_mode == "shadow"
        and settings.link_expand_enabled
        and bool(state.raw_results)
    )
    authority_boosted_count = 0
    if authority_boost_enabled:
        for result in state.raw_results:
            incoming = result.get("incoming_link_count") or 0
            if incoming > 0:
                result["score"] = result["score"] + settings.link_authority_boost * math.log(
                    1 + incoming
                )
                authority_boosted_count += 1
    link_expand_step.meta("authority_boost_enabled", authority_boost_enabled)
    link_expand_step.meta("authority_boosted_count", authority_boosted_count)

    state.raw_results, state.page_context_candidate_boosted = _apply_page_context_boost(
        state.raw_results,
        state.page_context,
        mark=False,
    )
    state.decision_record["page_context_candidates_boosted"] = state.page_context_candidate_boosted


async def _run_rerank(state: RetrievalPipelineState) -> None:
    """Rerank candidates and apply the adjacent post-rerank boosts."""

    req = state.req
    # 5. Rerank (skip when reranker disabled)
    if state.raw_results and settings.reranker_enabled:
        t_rerank = time.perf_counter()
        rerank_input = state.raw_results[: settings.reranker_candidates]
        rerank_top_n = min(len(rerank_input), max(req.top_k, req.top_k * 3))
        async with state.trace.step("rerank", started_at=t_rerank) as rerank_step:
            rerank_step.meta("candidates_in", len(rerank_input))
            state.reranked = await reranker.rerank(state.query_resolved, rerank_input, rerank_top_n)
            rerank_step.meta("candidates_out", len(state.reranked))
        state.rerank_ms = rerank_step.duration_ms
        step_latency_seconds.labels(step="rerank").observe(state.rerank_ms / 1000)
        state.reranked_to = len(state.reranked)
        state.decision_record["rerank_ms"] = round(state.rerank_ms, 1)
        state.decision_record["reranker_scores_top5"] = [
            r.get("reranker_score") or r.get("score", 0) for r in state.reranked[:5]
        ]
    else:
        state.reranked = state.raw_results[: req.top_k]
        state.reranked_to = len(state.reranked)
        rerank_step = state.trace.mark_skipped(
            "rerank",
            "no_candidates" if not state.raw_results else "reranker_disabled",
            candidates_in=len(state.raw_results),
            candidates_out=state.reranked_to,
        )

    # REQ-RANK-01/04: in active mode every serving chunk gets the single
    # ranking truth; in shadow mode the field stays ABSENT on serving
    # chunks (downstream sorts then use their pre-contract keys) and a
    # slim preview copy replays the active pipeline for the shadow diff.
    if state.ranking_contract_mode == "active":
        _set_final_rank_scores(state.reranked)
    else:
        state.ranking_shadow_preview = [
            {key: chunk.get(key) for key in _SHADOW_PREVIEW_KEYS} for chunk in state.reranked
        ]
        _set_final_rank_scores(state.ranking_shadow_preview)

    # 5a-ter. SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-3 — link-expand
    # reranker boost. Applied AFTER rerank and BEFORE quality-floor so
    # expanded chunks get a fair shot at surviving source-aware-select.
    # Default boost=1.00 is a no-op until operator tunes the env var.
    link_expand_boost_enabled = (
        settings.link_expand_enabled and settings.link_expand_score_boost > 1.0
    )
    link_expand_boosted_count = sum(
        1
        for chunk in state.reranked
        if link_expand_boost_enabled
        and chunk.get("_link_expanded")
        and (
            isinstance(chunk.get("final_rank_score"), (int, float))
            or isinstance(chunk.get("reranker_score"), (int, float))
        )
    )
    state.reranked = _apply_link_expand_boost(
        state.reranked,
        boost=settings.link_expand_score_boost,
        enabled=settings.link_expand_enabled,
    )
    rerank_step.meta("link_expand_boost_enabled", link_expand_boost_enabled)
    rerank_step.meta("link_expand_boost_factor", settings.link_expand_score_boost)
    rerank_step.meta("link_expand_boosted_count", link_expand_boosted_count)
    if settings.reranker_enabled:
        state.reranked, page_context_boosted = _apply_page_context_boost(
            state.reranked,
            state.page_context,
        )
    else:
        page_context_boosted = state.page_context_candidate_boosted
    state.decision_record["page_context_boosted"] = page_context_boosted


def _run_quality_floor(state: RetrievalPipelineState) -> None:
    """Remove candidates below the configured quality floor."""

    # 5a-bis. Quality-floor filter (SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07).
    # Removes chunks explicitly degraded to quality_score=0.0 BEFORE the
    # source-quota algorithm picks candidates — otherwise a walled chunk
    # could burn a diversity slot. The default floor (0.05) cannot
    # accidentally filter neutral 0.5 chunks; an operator must set the
    # threshold > 0.5 explicitly.
    with state.trace.step("quality_floor") as quality_floor_step:
        quality_floor_step.meta("candidates_in", len(state.reranked))
        state.reranked, quality_floor_filtered = filter_quality_floor(
            state.reranked, floor=settings.retrieval_quality_floor
        )
        quality_floor_step.meta("candidates_out", len(state.reranked))
        quality_floor_step.meta("filtered_count", quality_floor_filtered)
    state.decision_record["quality_floor_filtered"] = quality_floor_filtered
    if quality_floor_filtered > 0:
        # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-08 — labelled by org_id so
        # per-tenant pollution is visible in Grafana. Only increment on
        # non-zero to keep metric cardinality predictable for tenants
        # whose chunks never trip the floor.
        quality_floor_filtered_total.labels(org_id=state.req.org_id).inc(quality_floor_filtered)


def _run_source_select(state: RetrievalPipelineState) -> None:
    """Apply source-aware selection to the reranked candidates."""

    req = state.req
    # 5b. Source-aware selection (SPEC-KB-021)
    # Replaces separate router + quota: uses reranker scores to decide.
    # scope=both may retrieve the canonical personal KB alongside org KBs.
    # A router preference is derived from the org source catalog, so it
    # must never boost a personal chunk merely because the labels match.
    state.excluded_preferred_kb_slugs = (
        {personal_kb_slug(req.user_id)} if req.scope == "both" and req.user_id else None
    )
    with state.trace.step("source_select") as source_select_step:
        source_select_step.meta("candidates_in", len(state.reranked))
        if settings.source_quota_enabled:
            state.reranked, source_meta = source_aware_select(
                state.reranked,
                top_n=req.top_k,
                max_per_source=settings.source_quota_max_per_source,
                preferred_labels=state.router_selected,
                preferred_kb_slugs=set(req.kb_slugs) if req.kb_slugs else None,
                excluded_preferred_kb_slugs=state.excluded_preferred_kb_slugs,
                source_preference_boost=settings.source_preference_boost,
            )
        else:
            source_select_step.skip("disabled_by_config")
            source_meta = {
                "source_select_mode": "disabled",
                "source_counts": {},
                "preference_applied": False,
                "preferred_labels": [],
                "boost": settings.source_preference_boost,
                "pack_without_preference": [],
                "suppressed_count": 0,
                "max_score_inversion": 0.0,
            }
        source_select_step.meta("candidates_out", len(state.reranked))
        source_select_step.meta("mode", source_meta["source_select_mode"])
        source_select_step.meta("preference_applied", source_meta["preference_applied"])
        source_select_step.meta("suppressed_count", source_meta["suppressed_count"])
        source_select_step.meta("max_score_inversion", source_meta["max_score_inversion"])
    state.decision_record["source_select"] = source_meta


def _run_quality_boost(state: RetrievalPipelineState) -> None:
    """Apply quality boost and finalize serving-order instrumentation."""

    req = state.req
    # 5c. Quality score boost (SPEC-KB-015 REQ-KB-015-19,20,21)
    with state.trace.step("quality_boost") as quality_boost_step:
        quality_boost_step.meta("candidates_in", len(state.reranked))
        state.reranked = quality_boost(
            state.reranked, contract_active=state.ranking_contract_mode == "active"
        )
        quality_boost_applied = any(r.get("feedback_count", 0) >= 3 for r in state.reranked)
        quality_boosted_count = sum(
            1
            for chunk in state.reranked
            if isinstance(chunk.get("feedback_count", 0), (int, float))
            and chunk.get("feedback_count", 0) >= 3
        )
        quality_boost_step.meta("candidates_out", len(state.reranked))
        quality_boost_step.meta("boosted_count", quality_boosted_count)
    state.decision_record["quality_boost_applied"] = quality_boost_applied
    if state.ranking_shadow_preview is not None:
        # REQ-RANK-04 shadow diff: replay the FULL post-rerank pipeline
        # (same steps, same settings) on the preview so "new" is what
        # active mode would actually serve — not a partial projection.
        # "old" is captured from the real ``serving`` list further down.
        state.ranking_shadow_preview = _apply_link_expand_boost(
            state.ranking_shadow_preview,
            boost=settings.link_expand_score_boost,
            enabled=settings.link_expand_enabled,
        )
        if settings.reranker_enabled:
            state.ranking_shadow_preview, _ = _apply_page_context_boost(
                state.ranking_shadow_preview,
                state.page_context,
            )
        state.ranking_shadow_preview, _ = filter_quality_floor(
            state.ranking_shadow_preview, floor=settings.retrieval_quality_floor
        )
        if settings.source_quota_enabled:
            state.ranking_shadow_preview, _ = source_aware_select(
                state.ranking_shadow_preview,
                top_n=req.top_k,
                max_per_source=settings.source_quota_max_per_source,
                preferred_labels=state.router_selected,
                preferred_kb_slugs=set(req.kb_slugs) if req.kb_slugs else None,
                excluded_preferred_kb_slugs=state.excluded_preferred_kb_slugs,
                source_preference_boost=settings.source_preference_boost,
            )
        state.ranking_shadow_preview = quality_boost(
            state.ranking_shadow_preview, contract_active=True
        )

    # 6. Serve the post-rerank pipeline order. The retired evidence-tier
    # experiment no longer has an environment-controlled alternate order.
    state.serving = state.reranked

    # REQ-RANK-04 shadow diff: "old" is the ACTUAL served list (this
    # request's response), "new" is the replayed active-contract
    # pipeline — the ≥7-day shadow review compares real serving deltas.
    if state.ranking_shadow_preview is not None:
        state.decision_record["ranking_contract_shadow"] = {
            "old": _ranking_contract_snapshot(
                state.serving, max_sources=state.evidence_max_sources
            ),
            "new": _ranking_contract_snapshot(
                state.ranking_shadow_preview, max_sources=state.evidence_max_sources
            ),
        }

    # F3 phase 1 instrumentation: emit link-expansion contribution to
    # the served top-k. Lets us answer "did the extra Qdrant scroll
    # ever produce a chunk that beat the reranker top-k cut-off?"
    # before deciding on phase 2 (RRF migration vs disable).
    # Audit ref: retrieval-coupling-2026-05-06 finding F3, link expansion
    # dead weight (historical — audit removed in repo cleanup 2026-08-18).
    state.expanded_in_top_k_ids = [r["chunk_id"] for r in state.serving if r.get("_link_expanded")]
    seed_in_top_k_ids = [
        r["chunk_id"] for r in state.serving if r["chunk_id"] in state.link_expand_seed_chunk_ids
    ]
    state.decision_record["link_expand"] = {
        "enabled": settings.link_expand_enabled,
        "seed_k": len(state.link_expand_seed_chunk_ids),
        "candidate_urls": state.link_expand_candidate_urls,
        "expanded_added": state.link_expand_count,
        "expanded_in_top_k": len(state.expanded_in_top_k_ids),
        "expanded_top_k_chunk_ids": state.expanded_in_top_k_ids,
        "seed_in_top_k": len(seed_in_top_k_ids),
        "served_top_k": len(state.serving),
    }

    # Issue #71 measurement gate: before adding any custom graph traversal,
    # prove whether the current Graphiti graph leg contributes to served
    # top-K at all. This is intentionally observability-only.
    state.graph_in_top_k_ids = [
        r["chunk_id"] for r in state.serving if r["chunk_id"] in state.graph_candidate_ids
    ]
    state.decision_record["graph_search"] = {
        "enabled": settings.graphiti_enabled,
        "candidates_returned": state.graph_results_count,
        "graph_in_top_k": len(state.graph_in_top_k_ids),
        "graph_top_k_chunk_ids": state.graph_in_top_k_ids,
        "served_top_k": len(state.serving),
    }


async def _run_parent_lookup(state: RetrievalPipelineState) -> None:
    """Fetch parent text for the selected child chunks."""

    # 6b. SPEC-RAG-PARENT-CHILD-001: swap child text for the parent's
    # broader-context text. Fetched in one batch query against
    # knowledge.parent_chunks. Children with no parent_chunk_id (legacy
    # ingests) keep their own text — REQ-3 fall-through.
    from retrieval_api.services import parent_lookup

    parent_id_per_serving: list[int | None] = [r.get("parent_chunk_id") for r in state.serving]
    parent_ids_requested = [pid for pid in parent_id_per_serving if pid is not None]
    async with state.trace.step("parent_lookup") as parent_lookup_step:
        state.parent_text_by_id = await parent_lookup.fetch_parents(parent_ids_requested)
        parent_lookup_step.meta("parent_ids_requested", len(parent_ids_requested))
        parent_lookup_step.meta("parents_found", len(state.parent_text_by_id))


def _run_response_build(state: RetrievalPipelineState) -> None:
    """Build response chunks, swapping parent text where available."""

    # 7. Build ChunkResult objects (with parent-text swap when available)
    state.chunks_out = []
    parent_text_chunks = 0
    with state.trace.step("response_build") as response_build_step:
        for result in state.serving:
            pid = result.get("parent_chunk_id")
            if pid is not None and pid in state.parent_text_by_id:
                display_text = state.parent_text_by_id[pid]
                is_parent = True
                parent_text_chunks += 1
            else:
                display_text = result["text"]
                is_parent = False
            state.chunks_out.append(
                ChunkResult(
                    chunk_id=result["chunk_id"],
                    artifact_id=result.get("artifact_id"),
                    content_type=result.get("content_type"),
                    text=display_text,
                    context_prefix=result.get("context_prefix"),
                    heading_path=result.get("heading_path"),
                    score=result.get("final_rank_score", result["score"])
                    if state.ranking_contract_mode == "active"
                    else result["score"],
                    reranker_score=result.get("reranker_score"),
                    scope=result.get("scope"),
                    valid_at=result.get("valid_at"),
                    invalid_at=result.get("invalid_at"),
                    ingested_at=result.get("ingested_at"),
                    assertion_mode=result.get("assertion_mode"),
                    source_ref=result.get("source_ref"),
                    source_connector_id=result.get("source_connector_id"),
                    source_url=result.get("source_url"),
                    kb_slug=result.get("kb_slug"),
                    source_label=result.get("source_label"),
                    title=result.get("title"),
                    original_filename=result.get("original_filename"),
                    image_urls=payload_list(result, "image_urls") or None,
                    entity_names=payload_list(result, "entity_names") or None,
                    is_parent_text=is_parent,
                )
            )
        response_build_step.meta("chunks_built", len(state.chunks_out))
        response_build_step.meta("parent_text_chunks", parent_text_chunks)


def _run_confidence_band(state: RetrievalPipelineState) -> None:
    """Compute confidence and build the evidence pack and final metrics."""

    req = state.req
    state.retrieval_ms = (time.perf_counter() - state.t0) * 1000
    step_latency_seconds.labels(step="total").observe(state.retrieval_ms / 1000)
    retrieval_requests_total.labels(scope=req.scope).inc()
    retrieval_chunks_total.labels(scope=req.scope).observe(len(state.chunks_out))

    # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 — confidence band on the
    # served chunks so downstream consumers can decide how strongly to answer.
    t_confidence = time.perf_counter()
    with state.trace.step("confidence_band", started_at=t_confidence):
        state.confidence_band = _compute_confidence_band(
            state.serving,
            high_threshold=settings.confidence_band_high_threshold,
            low_threshold=settings.confidence_band_low_threshold,
            reranker_enabled=settings.reranker_enabled,
        )
    state.trace.meta("confidence_band", state.confidence_band)
    retrieval_confidence_band_total.labels(band=state.confidence_band, org_id=req.org_id).inc()

    evidence_query = (
        f"{req.raw_query}\n{state.query_resolved}"
        if req.raw_query and req.raw_query != state.query_resolved
        else state.query_resolved
    )
    state.evidence_pack = build_evidence_pack(
        state.chunks_out,
        query=evidence_query,
        max_sources=state.evidence_max_sources,
    )
    state.decision_record["evidence_pack"] = {
        "item_count": len(state.evidence_pack.items),
        "source_count": len(state.evidence_pack.sources),
        "no_citable_reason": state.evidence_pack.no_citable_reason,
        "top_source_labels": [
            source.source_label or source.title for source in state.evidence_pack.sources[:3]
        ],
        "sources": _evidence_pack_decision_sources(state.evidence_pack),
        "top_item_chunk_ids": [item.chunk_id for item in state.evidence_pack.items[:5]],
    }

    # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-8 — link-expand survival
    # outcome counter. Only count when link-expand actually contributed
    # candidates (link_expand_count > 0). Hit = at least one expanded chunk
    # survived to the served top-K; miss = none did.
    if state.link_expand_count > 0:
        outcome = "hit" if state.expanded_in_top_k_ids else "miss"
        retrieval_link_expand_top_k_total.labels(outcome=outcome, org_id=req.org_id).inc()

    # Issue #71: only count requests where Graphiti returned candidates. Empty
    # graph searches are measured by graph_results_count and should not dilute
    # the contribution ratio.
    if state.graph_results_count > 0:
        outcome = "hit" if state.graph_in_top_k_ids else "miss"
        retrieval_graph_top_k_total.labels(outcome=outcome, org_id=req.org_id).inc()

    state.trace.meta("total_ms", round(state.retrieval_ms, 1))
    state.trace.record_ok("total", state.retrieval_ms)


def _record_decision_and_shadow(state: RetrievalPipelineState) -> None:
    """Emit the decision record, shadow write, and summary log in order."""

    req = state.req
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
    if state.effective_level != "full":
        telemetry_level_decisions_total.labels(
            level=state.effective_level, decision="metadata_only"
        ).inc()
    elif state.effective_level == "full":
        telemetry_level_decisions_total.labels(level="full", decision="content_emitted").inc()

    try:
        for key, value in state.decision_record.items():
            state.trace.meta(key, value)
        logger.info("retrieval_decision_record", **state.trace.to_log_kwargs())
    except Exception:
        logger.exception("retrieval_trace_emit_failed")

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
    if state.effective_level in ("shadow", "full"):
        request_id_for_shadow = state.trace.request_id
        chunk_ids_for_shadow = [c.chunk_id for c in state.chunks_out]
        reranker_scores = [
            c.reranker_score for c in state.chunks_out if c.reranker_score is not None
        ]
        reranker_top1 = max(reranker_scores) if reranker_scores else None
        features_dict = extract_features(req.query)
        write_shadow(
            request_id=request_id_for_shadow,
            org_id=req.org_id,
            embedding=list(state.query_vector) if state.query_vector is not None else None,
            features=features_dict,
            band=state.confidence_band,
            chunk_ids=chunk_ids_for_shadow,
            reranker_top1=reranker_top1,
        )
        telemetry_level_decisions_total.labels(
            level=state.effective_level, decision="shadow_inserted"
        ).inc()
        if state.effective_level == "full":
            telemetry_level_decisions_total.labels(level="full", decision="full_logged").inc()

    logger.info(
        "retrieve",
        org_id=req.org_id,
        scope=req.scope,
        top_k=req.top_k,
        candidates_retrieved=state.candidates_retrieved,
        graph_results_count=state.graph_results_count,
        coref_ms=round(state.coref_ms, 1),
        embed_ms=round(state.embed_ms, 1),
        qdrant_ms=round(state.qdrant_ms, 1) if state.qdrant_ms is not None else None,
        retrieval_ms=round(state.retrieval_ms, 1),
        graph_search_ms=round(state.graph_search_ms, 1)
        if state.graph_search_ms is not None
        else None,
        rerank_ms=round(state.rerank_ms, 1) if state.rerank_ms is not None else None,
        link_expand_ms=round(state.link_expand_ms, 1) if state.link_expand_ms is not None else None,
        link_expand_count=state.link_expand_count,
    )


def _emit_product_event(state: RetrievalPipelineState) -> None:
    """Emit the verified-identity knowledge.queried product event."""

    req = state.req
    request = state.request
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

    if getattr(request.state, "klai_sub_query_internal", False):
        # This call is a sub-question leg of a fan-out request (see
        # ``_retrieve_sub_queries``): the caller emits ONE knowledge.queried
        # event for the original question after the fan-out completes, not
        # one per sub-question.
        pass
    elif event_tenant_id is not None:
        emit_event(
            "knowledge.queried",
            tenant_id=event_tenant_id,
            user_id=event_user_id,
            properties={
                "scope": req.scope,
                "kb_slugs": list(req.kb_slugs) if req.kb_slugs else [],
                "had_results": len(state.chunks_out) > 0,
                "result_count": len(state.chunks_out),
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


def _build_response(state: RetrievalPipelineState) -> RetrieveResponse:
    """Assemble the public retrieve response from completed pipeline state."""

    return RetrieveResponse(
        query_resolved=state.query_resolved,
        # Compatibility field for rolling callers; the retired gate means this
        # service can no longer produce a bypassed retrieval response.
        retrieval_bypassed=False,
        chunks=state.chunks_out,
        metadata=RetrieveMetadata(
            candidates_retrieved=state.candidates_retrieved,
            reranked_to=state.reranked_to,
            retrieval_ms=round(state.retrieval_ms, 1),
            rerank_ms=round(state.rerank_ms, 1) if state.rerank_ms is not None else None,
            graph_results_count=state.graph_results_count,
            graph_search_ms=round(state.graph_search_ms, 1)
            if state.graph_search_ms is not None
            else None,
        ),
        confidence_band=state.confidence_band,
        evidence_pack=state.evidence_pack,
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    req: RetrieveRequest,
    request: Request,
    # SPEC-SEC-SERVICE-AUTH-001 REQ-3: scope check. JWT callers must hold
    # ``klai:internal:retrieval:query``. Internal-secret callers bypass
    # during Phase B/C — see ``require_scope`` docstring.
    _auth: AuthContext = _REQUIRE_RETRIEVAL_SCOPE,
) -> RetrieveResponse:
    preamble_result = await _resolve_identity_and_telemetry(req, request, _auth)
    if isinstance(preamble_result, RetrieveResponse):
        return preamble_result
    state = preamble_result
    await _run_coreference(state)
    await _run_embed(state)

    # The retired gate has no runtime work. Its explicit skipped step keeps the
    # trace vocabulary stable without reintroducing retrieval bypass behavior.
    state.trace.mark_skipped("gate", "disabled_by_config")

    await _run_router(state)
    await _run_search(state)
    # F3 phase 1 instrumentation (audit retrieval-coupling-2026-05-06):
    # capture seed/expanded sets across the pipeline so the final
    # decision_record can measure link-expansion's contribution to
    # the served top-k. Without this, we cannot tell whether the
    # expanded chunks ever survive the reranker + source-select +
    # quality-boost pass — i.e. whether the
    # extra Qdrant scroll-call buys anything in practice.
    await _run_link_expand(state)

    await _run_rerank(state)
    _run_quality_floor(state)
    _run_source_select(state)
    _run_quality_boost(state)

    await _run_parent_lookup(state)
    _run_response_build(state)
    _run_confidence_band(state)

    _record_decision_and_shadow(state)
    _emit_product_event(state)
    return _build_response(state)
