---
id: SPEC-RETRIEVAL-TRACE-001
version: "0.3.0"
status: draft
created: 2026-05-08
updated: 2026-09-01
author: Mark Vletter
priority: high
related:
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001
  - SPEC-PRIVACY-QUERY-SHADOW-001
  - SPEC-SEC-IDENTITY-ASSERT-001
  - SPEC-RAG-PARENT-CHILD-001
  - SPEC-KB-021
references:
  - docs/architecture/knowledge-retrieval-flow.md
  - docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md
  - .claude/rules/klai/projects/knowledge.md
  - klai-retrieval-api/retrieval_api/api/retrieve.py
---

# HISTORY

| Datum | Versie | Wijziging |
|-------|--------|-----------|
| 2026-09-01 | 0.3.0 | De `/retrieve`-handler is conform A4 gedragbehoudend opgesplitst in functies per pipelinestap, met één kleine mutable state-dataclass en zonder pipelineframework, stepklassen, registry of config-driven dispatch. |
| 2026-09-01 | 0.2.0 | De trace is compleet voor alle live `/retrieve`-pipelinestappen. De kandidaattransformaties zijn gewrapt en gedeelde Prometheus-/trace-duren worden eenmaal gemeten; retired gate/evidence-tier-gedrag is niet heringevoerd. |
| 2026-09-01 | 0.1.1 | Implementation note: op origin/main zijn de gate- en evidence-tier-stappen inmiddels uit de pipeline verwijderd (commit 2ce085277, "remove retired retrieval experiments"). De trace rendert voor die stappen compatibiliteitsdefaults / een skipped step; er is geen gate-gedrag heringevoerd. AC-3 is daardoor niet runtime-bereikbaar en geldt als vervallen zolang de gate retired is. |
| 2026-05-08 | 0.1.0 | Initial draft. Introduceert `RetrievalTrace` en kleine step wrappers rond `/retrieve` als incrementele vervanging voor ad-hoc mutaties op `decision_record`, met behoud van het bestaande `retrieval_decision_record` logcontract. |

---

# SPEC-RETRIEVAL-TRACE-001: RetrievalTrace voor /retrieve

## Summary

Introduceer een typed `RetrievalTrace` object en dunne step wrappers in `klai-retrieval-api/retrieval_api/api/retrieve.py`, zodat de lange `/retrieve` pipeline haar beslissingen, timings, skips en errors consistent vastlegt zonder een volledige rewrite van de retrieval flow.

De eerste versie is bewust pragmatisch:

- geen nieuwe service;
- geen schema-migratie;
- geen verandering aan retrieval-ranking of promptgedrag;
- geen breaking change in het bestaande `retrieval_decision_record` event;
- wel een centrale typed trace die het huidige mutable `decision_record: dict = {}` vervangt als primaire schrijfplek.

Het einddoel is niet "mooie code" op zichzelf. Het doel is dat toekomstige retrieval-wijzigingen, zoals extra chunkvelden, router-lagen, parent-expansion, privacy-gating en confidence-band tuning, niet opnieuw door de handler heen handmatig timings en dict keys hoeven te threaden.

## Motivation

`/retrieve` is inmiddels de drukste retrieval-pipeline van Klai. De handler doet in een enkel pad:

- identity verification;
- canonical telemetry-level resolving;
- coreference rewrite;
- dense + sparse embedding;
- gate decision;
- router source selection;
- Qdrant search;
- optional Graphiti search;
- link expansion;
- rerank;
- link-expand score boost;
- quality-floor filtering;
- source-aware selection;
- quality boost;
- evidence-tier shadow scoring;
- parent-child text expansion;
- `ChunkResult` assembly;
- confidence-band emit;
- privacy-gated `retrieval_decision_record`;
- shadow telemetry write;
- `knowledge.queried` product event.

De huidige observability groeit mee via een mutable `decision_record` dict die op veel plekken in de handler wordt aangepast. Dat werkt, maar het heeft drie terugkerende risico's:

1. **Field threading bugs.** Klai heeft al bugs gehad waarbij een retrieval field door een van de serialisatiegrenzen wegviel. De kennisregel noemt expliciet de 7 lagen van Qdrant payload naar frontend citation.
2. **Observability drift.** Nieuwe stappen voegen eigen timing keys, counters en logvelden toe. Daardoor is niet altijd duidelijk of een stap succesvol, skipped, disabled, bypassed of failed was.
3. **Privacy-gating is te laat en te handmatig.** `effective_level` wordt correct bepaald en raw query content wordt nu vlak voor emit uit `decision_record` verwijderd. Nieuwe tracevelden kunnen dat per ongeluk omzeilen als ze raw content buiten de bekende keys stoppen.

Deze SPEC maakt observability een first-class contract voor `/retrieve`: elke pipeline-stap registreert dezelfde shape, en compatibiliteit met het bestaande `retrieval_decision_record` blijft de migratiegrens.

## Scope

### In scope

**Retrieval-api**

- Nieuwe typed trace module, bijvoorbeeld `retrieval_api/tracing.py`.
- `RetrievalTrace` object dat per request wordt aangemaakt in `/retrieve`.
- `trace.step(...)` wrapper/contextmanager voor sync en async stappen.
- Step metadata: `name`, `status`, `duration_ms`, `skipped_reason`, `error_type`, `error_message_safe`, en optionele typed details.
- Centrale privacy/telemetry-level gating voordat trace data naar structlog of shadow-store gaat.
- Backwards-compatible render naar het bestaande `retrieval_decision_record` event.
- Tests rond trace shape, timing, skipped/error metadata, privacy gating en compatibiliteit.
- Incrementele migratie van `/retrieve`: start met de stappen waar vandaag al timing/decision-record velden bestaan; laat rankinggedrag ongemoeid.

**Documentation / runbook**

- Korte developer-notitie in de SPEC/PR over hoe nieuwe retrieval-stappen trace data moeten toevoegen.
- Geen brede architectuurdoc rewrite in deze SPEC.

### Out of scope

- Volledige opsplitsing van `retrieve.py` in een pipeline framework.
- Nieuwe distributed tracing backend, OpenTelemetry spans, Jaeger, of vendor-integratie.
- Nieuwe product-events of BI-schema's.
- Verandering aan ranking, reranker, graph search, link expansion, parent expansion, confidence thresholds of prompt-injectie.
- Wijziging van de response body van `/retrieve`, behalve als een toekomstige SPEC apart een debug-only trace response contract definieert.
- Retentieconfiguratie in VictoriaLogs. Deze SPEC respecteert de bestaande telemetry-level en retention-class contracten.

## Functional Requirements

### REQ-1 -- Typed trace object

**THE retrieval-api SHALL** introduce a typed `RetrievalTrace` object for one `/retrieve` request.

Minimum constructor inputs:

- `request_id: str`
- `org_id: str`
- `scope: str`
- `telemetry_level: Literal["off", "shadow", "full"]`
- `started_at: float` or equivalent monotonic timestamp

The object SHALL own:

- ordered step records;
- top-level decision fields that are not naturally tied to one step;
- compatibility rendering for `retrieval_decision_record`;
- privacy-gated rendering for logs.

The object MUST NOT perform retrieval work itself. It is an observability/data-collection helper, not a pipeline orchestrator.

### REQ-2 -- Step wrapper records status consistently

**THE retrieval-api SHALL** provide a step wrapper for retrieval pipeline sections. For every wrapped step it MUST record:

- `name`;
- `status`: `ok`, `skipped`, or `error`;
- `duration_ms`;
- `skipped_reason` when status is `skipped`;
- `error_type` and safe error metadata when status is `error`;
- optional step-specific fields.

The wrapper MUST preserve existing exception semantics. If a step currently raises and aborts the request, wrapping it MUST still raise. If a step currently catches and logs a failure, wrapping it MUST record `status=error` and allow the existing fail-open behavior to continue.

Initial step names:

| Step | Existing code surface |
|------|-----------------------|
| `coreference` | `coreference.resolve` |
| `embed` | `embed_single` + `embed_sparse` gather |
| `gate` | `gate.should_bypass` |
| `router` | `fetch_source_catalog` + `route_to_sources` |
| `qdrant_search` | `search.hybrid_search` |
| `graph_search` | `graph_search.search` |
| `link_expand` | `search.fetch_chunks_by_urls` |
| `rerank` | `reranker.rerank` |
| `quality_floor` | `filter_quality_floor` |
| `source_select` | `source_aware_select` |
| `quality_boost` | `quality_boost` |
| `evidence_tier` | `evidence_tier.apply` |
| `parent_lookup` | `parent_lookup.fetch_parents` |
| `response_build` | `ChunkResult(...)` assembly |
| `confidence_band` | `_compute_confidence_band` |

Deliberately **not** separate steps in v1:

- `write_shadow` and `emit_event("knowledge.queried", ...)` run **after** the `retrieval_decision_record` log emission in the current pipeline. Including them as trace steps would require deferring the log emission until after those calls — an ordering/behavior change that does not belong in an observability-only PR. They keep their existing own log lines; a follow-up SPEC may fold them in if the emission point moves.
- The two score-mutating micro-blocks (authority boost, SPEC-CRAWLER-003 R17, between link-expand and rerank; and `_apply_link_expand_boost`, SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-3, after rerank) are recorded as metadata fields on the adjacent named step (`link_expand` resp. `rerank`), not as separate steps — this keeps the step vocabulary bounded while still satisfying REQ-9 completeness.

### REQ-3 -- Backwards-compatible retrieval_decision_record

**THE trace SHALL** render a flat dict compatible with the current `retrieval_decision_record` consumers.

The first implementation MUST keep existing top-level keys where dashboards, alerts, docs, or recent SPECs mention them, including at least:

- `coreference_rewrite` when telemetry allows content;
- `coreference_ms`;
- `embedding_ms`;
- `gate_margin`;
- `gate_bypassed`;
- `gate_ms`;
- `router`;
- `search_ms`;
- `search_candidates_count`;
- `rerank_ms`;
- `reranker_scores_top5`;
- `quality_floor_filtered`;
- `source_select`;
- `quality_boost_applied`;
- `evidence_shadow_mode`;
- `link_expand`;
- `confidence_band`;
- `total_ms`;
- `retention_class`.

The render MAY add a nested `trace_steps` array. It MUST NOT remove existing fields in the first migration PR.

### REQ-4 -- Privacy and telemetry-level gating at trace boundary

**THE trace SHALL** centralize privacy gating before any trace data is emitted.

Rules:

- In `telemetry_level != "full"`, raw query text and resolved query text MUST NOT be emitted in `retrieval_decision_record`.
- In `telemetry_level == "full"`, content-bearing fields MAY be emitted exactly as today.
- Every step detail field MUST declare whether it is content-bearing or metadata-only, either by explicit API (`trace.content(...)` vs `trace.meta(...)`) or by typed field definition.
- The rendered record MUST set `retention_class="content"` only when content-bearing fields are present and allowed.
- Defense-in-depth structlog processors remain in place; this SPEC does not rely on them as the primary gate.

### REQ-5 -- Skipped metadata is explicit

**WHEN** a step is skipped due to gate bypass, disabled config, no candidates, no candidate URLs, shadow mode, or missing verified identity, **THE trace SHALL** record a step with `status="skipped"` and a stable `skipped_reason`.

Minimum skipped reasons:

- `gate_bypassed`
- `disabled_by_config`
- `no_candidates`
- `no_candidate_urls`
- `reranker_disabled`
- `shadow_mode`
- `no_verified_identity`

This replaces the current pattern where missing timing fields imply skip state.

### REQ-6 -- Error metadata is safe and useful

**WHEN** a wrapped fail-open step catches an exception, **THE trace SHALL** record:

- `status="error"`;
- `error_type`, e.g. `TimeoutError`;
- `error_message_safe` only when it is known not to contain query text, credentials, source text, or payload dumps;
- no raw traceback in the trace payload.

The normal logger MAY still emit `exc_info=True` on existing warning/error logs where appropriate. The trace payload itself must remain safe for telemetry-level `shadow`.

### REQ-7 -- Metrics remain stable

**THE implementation SHALL NOT** remove or rename existing Prometheus metrics as part of this SPEC.

Existing calls to `step_latency_seconds`, `retrieval_confidence_band_total`, `retrieval_link_expand_top_k_total`, `telemetry_level_decisions_total`, `retrieval_requests_total`, `retrieval_chunks_total`, and `quality_floor_filtered_total` may move behind helper functions only if their label cardinality and increment/observe semantics remain unchanged.

Note: the Prometheus `step` label vocabulary in `metrics.py` (`coref`, `qdrant`, `graph`, `embed`, ...) intentionally differs from the trace `StepName` vocabulary (`coreference`, `qdrant_search`, `graph_search`, `embed`, ...). The two are not meant to be joined; do NOT rename the Prometheus labels to match the trace — that would break existing dashboards.

### REQ-8 -- Incremental migration, no pipeline rewrite

**THE implementation SHALL** migrate `/retrieve` in small slices:

1. Add `RetrievalTrace` and tests without changing `/retrieve`.
2. Wrap low-risk pure/timing-only steps first (`coreference`, `embed`, `gate`, `confidence_band`, `total`).
3. Wrap optional/fail-open steps next (`router`, `graph_search`, `link_expand`).
4. Wrap candidate-transform steps last (`qdrant_search`, `rerank`, `quality_floor`, `source_select`, `quality_boost`, `evidence_tier`, `parent_lookup`, `response_build`).

Each slice MUST keep tests green before the next slice starts. Slices 1-3 are the scope of the first implementation PR; slice 4 is an explicit follow-up PR once slices 1-3 have proven stable (it carries the highest regression surface for the least incremental observability value).

### REQ-9 -- Trace helper enforces stable step names

**THE trace module SHALL** define step names as constants or a `Literal`/enum type. New ad-hoc string names in `/retrieve` are not allowed once the helper exists.

This is to keep Grafana, VictoriaLogs queries, and regression tests from drifting due to spelling differences like `qdrant`, `search`, and `qdrant_search`.

### REQ-10 -- Tests cover compatibility and privacy

**THE implementation SHALL** add tests that prove:

- existing flat `retrieval_decision_record` keys still render;
- `trace_steps` order follows pipeline order;
- skipped steps include stable reasons;
- fail-open errors become `status="error"` without changing response behavior;
- `telemetry_level="shadow"` removes raw query and resolved query text;
- `telemetry_level="full"` keeps content-bearing fields where current behavior does;
- gate-bypassed requests still emit total/gate metadata and mark downstream retrieval steps skipped.

## Non-Functional Requirements

- **Latency:** wrapper overhead MUST be below 1 ms p95 per request on local unit/benchmark coverage. The implementation should use `time.perf_counter()` and simple dataclasses/Pydantic-free internal records unless a stronger reason appears.
- **Compatibility:** existing dashboards and VictoriaLogs queries against `retrieval_decision_record` MUST continue to work during the first release.
- **Fail-open:** trace emission failure MUST NOT fail `/retrieve`. If rendering the trace raises unexpectedly, retrieval should return normally and log `retrieval_trace_emit_failed`.
- **Low cardinality:** step names and skipped reasons MUST be bounded constants. No query text, chunk IDs, URLs, exception messages, or tenant-specific values in metric labels.
- **No cross-service lockstep deploy:** LiteLLM-hook, portal-api, MCP, and frontend must not need same-day changes.

## Architecture Decisions

### A1. Trace object, not pipeline framework

`RetrievalTrace` is intentionally a sidecar. It records what the existing handler did; it does not decide what the handler should do. That keeps the first migration small and avoids entangling observability with ranking behavior.

### A2. Flat compatibility render first

The current `retrieval_decision_record` event is already used by low-confidence diagnostics and privacy-level validation. The first release keeps the flat keys and adds structure beside them. A later SPEC may deprecate flat keys after dashboards and runbooks have moved.

### A3. Privacy at write API boundaries

Trace call sites should make content-vs-metadata explicit when writing. The final renderer still enforces policy, but the API should make it hard to accidentally store raw query text in a metadata field.

### A4. Step wrappers around existing blocks

The handler stays readable during migration: wrap existing blocks, do not extract every step into a new class. Once trace coverage is stable, future refactors can split the pipeline with real behavioral tests in place.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Compatibility render accidentally changes a `retrieval_decision_record` key | medium | high | Golden tests against a representative happy-path, gate-bypass path, and graph-failure path. First PR keeps old keys top-level. |
| Trace wrapper hides or swallows an exception | low | high | Wrapper API must default to re-raise. Fail-open behavior is opt-in and tested per step. |
| New `trace_steps` array leaks query text in shadow/off mode | medium | high | Content-vs-metadata API plus explicit tests for shadow/off rendering. |
| Added structure makes the already-long handler harder to read | medium | medium | Migrate in slices and remove old duplicated timing variables as each step moves. No broad extraction until after trace tests are in place. |
| Metrics and trace disagree on timing | medium | low | Use the same measured duration for both trace record and `step_latency_seconds.observe()` where practical. |

## Open Questions

1. Should `trace_steps` include chunk IDs for debugging when `telemetry_level="full"`? Initial answer: no. Keep chunk IDs in shadow-store/write-shadow paths and existing response logs, not per-step trace.
2. Should `RetrievalTrace` live under `retrieval_api/tracing.py` or `retrieval_api/services/trace.py`? Initial preference: top-level `retrieval_api/tracing.py`, because it is not a retrieval service.
3. Should skip reasons be emitted to Prometheus as labels? Initial answer: no in v0.1. VictoriaLogs can query `trace_steps`; metrics cardinality stays unchanged.

## Internal References

Note on `related`: `SPEC-PRIVACY-QUERY-SHADOW-001` has no spec directory under `.moai/specs/` anymore — it was implemented and archived. Its requirements survive as code comments in `retrieve.py` and `metrics.py`; treat those as the authoritative reference.

- [Knowledge Retrieval Flow](../../../docs/architecture/knowledge-retrieval-flow.md)
- [Low-Confidence Abstain implementation notes](../../../docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md)
- [Knowledge Domain Patterns](../../../.claude/rules/klai/projects/knowledge.md)
- [`retrieve.py`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py)
