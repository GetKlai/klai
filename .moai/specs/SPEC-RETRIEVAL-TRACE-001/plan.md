---
id: SPEC-RETRIEVAL-TRACE-001
plan_for: spec.md
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
---

# Implementation Plan -- SPEC-RETRIEVAL-TRACE-001

## 0. Pre-flight

1. Run the focused retrieval-api tests before changing code:
   ```bash
   cd klai-retrieval-api
   uv run pytest tests/test_api.py tests/test_confidence_band.py
   ```
2. Capture one representative current `retrieval_decision_record` from a local happy-path test or fixture. Use it as the compatibility target for flat keys.
3. Confirm `telemetry_level` behavior from `retrieve.py`: canonical level is resolved before pipeline work, `structlog.contextvars` is bound, and current privacy gating removes `coreference_rewrite` unless `effective_level == "full"`.

## 1. Unit 1 -- Trace module with no call-site migration

Add `klai-retrieval-api/retrieval_api/tracing.py`.

Suggested internal types:

```python
TraceStatus = Literal["ok", "skipped", "error"]
TelemetryLevel = Literal["off", "shadow", "full"]
StepName = Literal[
    "coreference",
    "embed",
    "gate",
    "router",
    "qdrant_search",
    "graph_search",
    "link_expand",
    "rerank",
    "quality_floor",
    "source_select",
    "quality_boost",
    "evidence_tier",
    "parent_lookup",
    "response_build",
    "confidence_band",
    "total",
]
```

(`shadow_write` and `product_event` are deliberately absent — they run after the
`retrieval_decision_record` emission; see spec REQ-2.)

Keep implementation lightweight: dataclasses and `time.perf_counter()`. Avoid Pydantic for internal trace objects unless tests show a need.

Tests:

- `klai-retrieval-api/tests/test_retrieval_trace.py`
- construct trace, add metadata, add content, render under `full`, `shadow`, and `off`;
- assert content is absent outside `full`;
- assert `retention_class` follows rendered content.

## 2. Unit 2 -- Compatibility renderer

Implement render helpers that can produce:

- `to_decision_record()` -- flat compatibility dict plus optional `trace_steps`;
- `to_log_kwargs()` -- safe kwargs for `logger.info("retrieval_decision_record", ...)`;
- `mark_skipped(name, reason, **metadata)`;
- `record_ok(name, duration_ms, **metadata)`;
- `record_error(name, exc, duration_ms, safe_message=None, **metadata)`.

Before touching `/retrieve`, add golden tests that build a trace manually and assert the flat keys match current names:

- `coreference_ms`;
- `embedding_ms`;
- `gate_margin`;
- `gate_bypassed`;
- `search_ms`;
- `rerank_ms`;
- `link_expand`;
- `confidence_band`;
- `total_ms`;
- `retention_class`.

## 3. Unit 3 -- Low-risk migration in retrieve.py

Create `trace = RetrievalTrace(...)` after `effective_level` is resolved and `request_id` can be read from structlog contextvars or generated.

Migrate only these fields first:

- `coreference_rewrite`
- `coreference_ms`
- `embedding_ms`
- `gate_margin`
- `gate_bypassed`
- `gate_ms`
- `confidence_band`
- `total_ms`
- `retention_class`

Keep the existing local variables (`query_resolved`, `query_vector`, `bypassed`, etc.) unchanged. The trace should replace writes to `decision_record`, not control the pipeline.

Tests:

- existing retrieval-api tests remain green;
- new test asserts `retrieval_decision_record` still contains old flat keys.

## 4. Unit 4 -- Optional/fail-open steps

Migrate optional steps where explicit skip/error state is highest value:

- `router`: skipped for `kb_slugs is not None`, router disabled, scope not org/both, gate bypassed, not enough source labels.
- `graph_search`: skipped when `graphiti_enabled` false; `error` when graph task fails and current code logs warning.
- `link_expand`: skipped when disabled, no raw results, no candidate URLs. Record the authority-boost application (SPEC-CRAWLER-003 R17) as metadata on this step.

`shadow_write` and `product_event` are NOT wrapped: they execute after the `retrieval_decision_record` log emission and cannot appear in the emitted trace without reordering the pipeline (out of scope for an observability-only change).

Preserve all existing log lines and metrics. The trace adds shape; it does not replace operational warnings yet.

**This unit is the end of the first implementation PR.** Units 5 and 6 below are an explicit follow-up PR, to start only after Units 1-4 have landed and proven stable.

## 5. Unit 5 -- Candidate-transform steps (FOLLOW-UP PR — not in first implementation)

Wrap the remaining candidate-transform sections:

- `qdrant_search`
- `rerank`
- `quality_floor`
- `source_select`
- `quality_boost`
- `evidence_tier`
- `parent_lookup`
- `response_build`

At this point remove duplicated timing variables only when the trace value is already proven by tests. Do not combine behavioral refactors with this unit.

## 6. Unit 6 -- Metrics cleanup and docs (docs part lands with the first PR; metrics-dedup part follows Unit 5)

Where a step duration is recorded in both trace and `step_latency_seconds`, use a single measured duration for both. Do not rename metrics or labels.

Add a short PR note or code comment near `RetrievalTrace`:

- use `trace.meta(...)` for counts, booleans, scores, durations;
- use `trace.content(...)` for raw/resolved query text;
- prefer stable skip reasons;
- never put payload dumps, chunk text, credentials, URLs, or source text into error metadata.

## 7. File-impact summary

Expected files:

```text
klai-retrieval-api/
  retrieval_api/
    tracing.py                  # NEW
    api/retrieve.py             # incremental trace writes/wrappers
  tests/
    test_retrieval_trace.py     # NEW
    test_api.py                 # extend only where needed for log-shape assertions
```

No migrations, no frontend changes, no LiteLLM-hook changes, no deploy config changes.

## 8. Pre-merge checklist

- [ ] Existing flat `retrieval_decision_record` keys preserved.
- [ ] `trace_steps` present and ordered in at least one happy-path log assertion.
- [ ] Gate-bypass path marks downstream retrieval steps skipped.
- [ ] Graph-search fail-open path records `status=error` and still returns response.
- [ ] `telemetry_level=shadow` and `off` render no raw/resolved query text.
- [ ] Existing Prometheus metric names and labels unchanged.
- [ ] Focused retrieval-api tests pass.
- [ ] PR description states this is observability-only and includes no ranking behavior change.

## 9. Rollback

Rollback is code-only:

1. Disable use of `RetrievalTrace` in `/retrieve` and restore direct `decision_record` writes.
2. Leave `retrieval_api/tracing.py` and tests in tree if harmless, or remove them in the rollback PR.
3. No data rollback required; emitted `trace_steps` are additive log fields.
