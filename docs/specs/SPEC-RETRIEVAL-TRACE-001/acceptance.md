---
id: SPEC-RETRIEVAL-TRACE-001
acceptance_for: spec.md
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
---

# Acceptance -- SPEC-RETRIEVAL-TRACE-001

This document is the executable contract for the SPEC. Every item maps to requirements in `spec.md`.

## How to run

Focused local test surface:

```bash
cd klai-retrieval-api
uv run pytest tests/test_retrieval_trace.py tests/test_api.py tests/test_confidence_band.py
```

If `tests/test_api.py` is too broad for the first implementation PR, run the existing focused retrieve tests plus the new trace tests and state the narrower command in the PR description.

## Acceptance Criteria

### AC-1 -- Trace object renders compatible decision record

**Given** a `RetrievalTrace` populated with a normal retrieval happy path,
**when** `to_decision_record()` is called,
**then** the rendered dict MUST contain the current flat keys:

- `coreference_rewrite` (only when telemetry level allows content)
- `coreference_ms`
- `embedding_ms`
- `gate_margin`
- `gate_bypassed`
- `gate_ms`
- `router`
- `search_ms`
- `search_candidates_count`
- `rerank_ms`
- `reranker_scores_top5`
- `quality_floor_filtered`
- `source_select`
- `quality_boost_applied`
- `evidence_shadow_mode`
- `link_expand`
- `confidence_band`
- `total_ms`
- `retention_class`

This list mirrors REQ-3 exactly — all 19 REQ-3 keys have an explicit check here. The dict MAY also contain `trace_steps`.

### AC-2 -- Step order is stable

**Given** a successful non-bypassed retrieval call,
**when** the `retrieval_decision_record` log is captured in tests,
**then** `trace_steps[*].name` MUST follow pipeline order for all executed steps:

`coreference -> embed -> gate -> router -> qdrant_search -> graph_search -> link_expand -> rerank -> quality_floor -> source_select -> quality_boost -> evidence_tier -> parent_lookup -> response_build -> confidence_band -> total`

`shadow_write` and `product_event` are NOT trace steps in v1 — they execute after the `retrieval_decision_record` emission (see spec REQ-2). Skipped steps may appear in the same order with `status="skipped"`. Steps in follow-up scope (spec REQ-8 slice 4) may be absent from `trace_steps` in the first PR as long as the relative order of present steps matches this list.

### AC-3 -- Gate bypass records explicit skips

**Given** `gate.should_bypass(...)` returns `True`,
**when** `/retrieve` completes,
**then** the response behavior MUST match current behavior and the trace MUST include skipped records for downstream retrieval work with `skipped_reason="gate_bypassed"` where applicable.

At minimum, `qdrant_search`, `graph_search`, `link_expand`, `rerank`, `parent_lookup`, and `response_build` MUST NOT appear as successful executed retrieval steps on a bypassed request.

### AC-4 -- Privacy gating removes content outside full telemetry

**Given** a trace containing raw query and resolved query content,
**when** it is rendered with `telemetry_level="shadow"` or `telemetry_level="off"`,
**then** the rendered decision record MUST NOT contain raw query text, resolved query text, or `coreference_rewrite`.

**And when** the same trace is rendered with `telemetry_level="full"`,
**then** current full-mode content behavior MAY be preserved and `retention_class` MUST be `content`.

### AC-5 -- Fail-open graph search remains fail-open

**Given** `settings.graphiti_enabled == true` and `graph_search.search(...)` raises,
**when** `/retrieve` completes,
**then** the endpoint MUST still return the Qdrant-based response as it does today,
**and** the trace MUST contain a `graph_search` step with:

- `status="error"`;
- `error_type` set;
- no raw query text;
- no traceback string.

### AC-6 -- Existing metrics are unchanged

**Given** the trace migration is complete,
**when** the focused retrieval tests run,
**then** no existing metric import or label expectation fails.

Manual review in the PR MUST confirm these names still exist:

- `step_latency_seconds`
- `retrieval_requests_total`
- `retrieval_chunks_total`
- `retrieval_confidence_band_total`
- `retrieval_link_expand_top_k_total`
- `telemetry_level_decisions_total`
- `quality_floor_filtered_total`

### AC-7 -- Trace emission failure does not fail retrieve

**Given** `RetrievalTrace.to_decision_record()` is forced to raise in a test double,
**when** `/retrieve` otherwise succeeds,
**then** the endpoint MUST still return a normal `RetrieveResponse`,
**and** a log event named `retrieval_trace_emit_failed` or equivalent MUST be emitted.

### AC-8 -- No ranking behavior changes

**Given** fixed mocks for search, rerank, quality floor, source selection, quality boost, evidence tier, and parent lookup,
**when** the same request is run before and after trace migration,
**then** returned chunk IDs, ordering, scores, `confidence_band`, and metadata MUST be identical.

This can be implemented as a golden retrieve test or by extending an existing `test_api.py` fixture.

### AC-9 -- Wrapper overhead is negligible

**Given** a unit benchmark or simple local timing test around a representative trace with all initial steps,
**when** 1,000 trace records are created and rendered,
**then** average overhead MUST be below 1 ms per request on a developer machine.

This is not a CI performance gate unless the project already has benchmark support. A PR note with local output is sufficient for v0.1.0.

### AC-10 -- Developer contract is documented

**Given** the SPEC implementation PR is ready,
**then** either `retrieval_api/tracing.py` docstring or the PR description MUST document:

- how to add a new step;
- when to use metadata vs content fields;
- allowed skipped reasons;
- that trace wrappers must preserve existing exception behavior.
