---
id: SPEC-RAG-EVAL-001
version: "0.2.0"
status: in_progress
created: 2026-05-04
updated: 2026-05-04
author: Mark Vletter
priority: high
related:
  - SPEC-RAG-CONTEXTUAL-001 (consumer of these metrics)
  - SPEC-RAG-QUERY-REWRITE-001 (consumer of these metrics)
  - SPEC-RAG-PARENT-CHILD-001 (consumer of these metrics)
  - SPEC-RAG-TAXONOMY-001 (consumer of these metrics)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# SPEC-RAG-EVAL-001: RAGAS Evaluation Harness

## Summary

Install [RAGAS](https://docs.ragas.io/) as the standing evaluation harness for Klai's retrieval pipeline. Without it, every Tier-2/3 retrieval improvement is anecdote-driven. With it, we can A/B compare optimisations against a reference query set per night and surface regressions in a Grafana panel.

This is the **precondition** for SPEC-RAG-CONTEXTUAL-001, SPEC-RAG-QUERY-REWRITE-001, SPEC-RAG-PARENT-CHILD-001 and SPEC-RAG-TAXONOMY-001 — without metrics in place, none of those SPECs can claim impact.

## Motivation

1. **Reference-free metrics.** RAGAS evaluates context-precision, context-recall, faithfulness, answer-relevance via LLM-as-judge — no ground-truth labels required. We can run on production traces.
2. **Pre-launch is the cheapest moment** to baseline the corpus. After launch, customer queries become the dataset; before, we control the seed query set.
3. **Decision data, not opinions.** When we ask "should we add HyDE?", the answer is "what does RAGAS show on the technical-query subset?" — not "the blog post said 42%".

## Scope

### In scope

**Backend — evaluation runner**

- New module `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` (or a new dedicated `klai-rag-eval` service if the install footprint is large; decision in plan-phase)
- Procrastinate task `evaluate_retrieval_quality_nightly` that:
  - Loads a YAML-defined query suite from `klai-knowledge-ingest/knowledge_ingest/eval/suites/` (one file per KB type: `chat.yaml`, `knowledge_org.yaml`, `knowledge_personal.yaml`)
  - For each query: calls retrieval-api with same auth as production litellm-hook
  - Captures retrieved chunks + a model answer via `klai-fast`
  - Runs RAGAS metrics: `context_precision`, `context_recall`, `faithfulness`, `answer_relevance`
  - Writes results to a new table `rag_eval_results` (org_id-scoped if we want per-tenant metrics, otherwise null/global)

**Database — results table**

```sql
CREATE TABLE rag_eval_results (
  id BIGSERIAL PRIMARY KEY,
  run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  suite TEXT NOT NULL,                  -- 'chat' / 'knowledge_org' / 'knowledge_personal'
  variant TEXT NOT NULL DEFAULT 'baseline',  -- 'baseline' / 'contextual_v1' / 'parent_child' / ...
  query_id TEXT NOT NULL,
  context_precision FLOAT,
  context_recall FLOAT,
  faithfulness FLOAT,
  answer_relevance FLOAT,
  retrieved_chunk_ids TEXT[],
  retrieval_ms INT,
  total_tokens INT,
  meta JSONB
);
CREATE INDEX ix_rag_eval_run_at_suite ON rag_eval_results (run_at DESC, suite);
CREATE INDEX ix_rag_eval_variant_run_at ON rag_eval_results (variant, run_at DESC);
```

**Query suite format**

```yaml
# klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml
suite: chat
description: Representative queries against the chat / personal-KB flow
queries:
  - id: chat-faq-onboarding-1
    org_zitadel_id: "test-tenant-1"
    user_zitadel_id: "test-user-1"
    query: "Hoe stel ik vakantie aan?"
    expected_topics: ["HR", "verlof"]    # for context_precision LLM-judge
  - id: chat-policy-1
    query: "What is the bring-your-own-device policy?"
    ...
```

The suite files start with **30 hand-written queries per type** (90 total). Generated as part of this SPEC's implementation by sampling production gap-event traces (when those exist) or by hand-curation against representative tenant data.

**Grafana panel**

- New PostgreSQL datasource panel "RAG quality (24h baseline)"
- Three time-series: `context_precision`, `context_recall`, `faithfulness` per suite
- Alert on `faithfulness < 0.85` for two consecutive runs (catastrophic regression)

**CI integration (optional, decided in plan-phase)**

- Workflow `rag-eval-pr.yml` runs the harness against a 10-query smoke subset on every PR that touches `klai-retrieval-api/`, `klai-knowledge-ingest/`, `klai-portal/backend/app/services/`, or `deploy/litellm/klai_knowledge.py`. Reports delta vs main in the PR comment.

### Out of scope

- Per-customer evaluation dashboards (post-launch)
- Custom LLM-as-judge models (use Mistral via klai-fast initially)
- Synthetic test-data generation (start with hand-written queries; revisit if the suite stagnates)
- Performance benchmarking (latency-only metrics; RAGAS focuses on quality)

## Acceptance Criteria (EARS)

- **REQ-1**: WHEN `evaluate_retrieval_quality_nightly` is invoked, the harness SHALL load all `.yaml` files in `eval/suites/` and run every query through the production retrieval path.
- **REQ-2**: For each query, the harness SHALL store one row in `rag_eval_results` with all four RAGAS metrics, retrieved chunk IDs, and elapsed retrieval time.
- **REQ-3**: WHEN a query's retrieval fails (HTTP error, timeout > 10s), the row SHALL still be inserted with NULL metrics and a `meta.error` field.
- **REQ-4**: The Grafana panel SHALL display a 7-day moving average of each metric per suite.
- **REQ-5**: The harness SHALL run at most one evaluation in parallel — concurrent runs against the same suite raise `RuntimeError`.
- **REQ-6**: WHEN a SPEC implementation team adds a `variant` column value (e.g. `contextual_v1`), the harness SHALL accept the variant via env var `RAG_EVAL_VARIANT` and tag every row with it; default `baseline`.
- **REQ-7**: An operator SHALL be able to run the harness ad-hoc via `python -m knowledge_ingest.eval.ragas_runner --suite chat --variant my-experiment` and see results within 5 minutes for a 30-query suite.

## Resolved Open Questions

1. **Service boundary** — RESOLVED: reuse `klai-knowledge-ingest`. RAGAS install footprint smoke-tested at 403 MB (scipy 90, pyarrow 83, pandas 37, numpy 41, sknetwork + PIL + langchain — no torch/transformers). Above the 200 MB heuristic but acceptable: scientific-stack libs the Docker base layer can cache, no GPU bloat, nightly job doesn't justify a new service container.
2. **LLM-as-judge cost** — RESOLVED: ~€5/month, NOT ~€54/month. Original estimate was wrong by an order of magnitude. Correct math: 60 queries × 4 metrics × 30 nights = 7,200 judge calls/month. klai-fast (Mistral small via LiteLLM) is ~€0.60 per million input tokens, ~€1.80 per million output tokens. 500 input + 50 output tokens per call → 3.6M input tokens (€2.16) + 0.36M output tokens (€0.65) ≈ €2.81/month + retry/buffer ≈ **~€5/month**.
3. **Per-tenant or global?** — RESOLVED: Voys-only for v1 (`org_zitadel_id = '368884765035593759'`). Multi-tenant by design — each query in a suite YAML carries its own `org_zitadel_id`, so adding tenants post-launch is a YAML drop-in (no service split, no per-tenant deployment).
4. **Variant comparison UI** — RESOLVED: Grafana SQL filter via `$variant` template variable on the `variant` column. No custom UI for v1.

## Estimated effort

3-5 days for one engineer:
- Day 1: install RAGAS, write 30 seed queries for `chat` suite, run end-to-end manually
- Day 2: Procrastinate task + DB schema + migration
- Day 3: 60 more queries (`knowledge_org` + `knowledge_personal` suites)
- Day 4: Grafana panel + alert
- Day 5: CI smoke harness on PR (optional, can defer)
