# Plan: SPEC-RAG-EVAL-001 — RAGAS Evaluation Harness

## 1. Overview

Stand up a RAGAS-based evaluation harness that runs nightly against Voys's production retrieval stack and writes per-query metrics (`context_precision`, `context_recall`, `faithfulness`, `answer_relevance`) to a new `rag_eval_results` table tagged with a `variant` column. The harness is the precondition for the four sibling SPECs — SPEC-RAG-CONTEXTUAL-001, SPEC-RAG-QUERY-REWRITE-001, SPEC-RAG-PARENT-CHILD-001, SPEC-RAG-TAXONOMY-001 — because each of them depends on the `variant` column to A/B-compare its experiment against the `baseline` row produced by this SPEC. Without the harness, every retrieval improvement claim is anecdotal.

The harness is **multi-tenant by design**: a single deployment in `klai-knowledge-ingest` runs against any tenant whose `org_zitadel_id` is present in a suite YAML. v1 ships with Voys-only suites (Voys is the only tenant with real content today). Adding more tenants post-launch is a YAML-only change — no service split, no per-tenant deployment.

## 2. Resolved Open Questions

| # | SPEC question | Resolution | Rationale |
|---|---------------|------------|-----------|
| 1 | Service boundary: `klai-knowledge-ingest` reuse vs new `klai-rag-eval` service? | **Reuse `klai-knowledge-ingest`** | Already runs Procrastinate, owns the asyncpg pool, ships migrations. RAGAS install footprint smoke-tested at 403 MB (scipy 90, pyarrow 83, pandas 37, numpy 41, sknetwork 15, PIL 15, langchain 12 — no torch/transformers). Above the 200 MB heuristic threshold but acceptable: it's scientific-stack libs the Docker base layer can cache, no GPU bloat, and a nightly job doesn't justify a new service container. |
| 2 | LLM-as-judge cost: SPEC estimates ~€54/month at 10,800 calls × 500 tokens. | **Actual cost ~€5/month.** Math: 90 queries × 4 metrics × 30 nights = 10,800 judge calls. klai-fast (Mistral small via LiteLLM) ≈ €0.60/M input tokens, €1.80/M output tokens. 500 input + 50 output tokens per call → 5.4M input + 0.54M output → €3.24 + €0.97 ≈ **€4.21/month**. Add ~€1 buffer for retries → **~€5/month**. | SPEC's number is wrong by an order of magnitude. The /run phase opens with a `spec.md` correction commit. |
| 3 | Per-tenant or global? | **Voys-only for v1** (`org_id = 368884765035593759`, slug `voys`, plan `professional`). | Reproducible, single curated KB, only tenant with real content today. Per-tenant generalisation deferred until alpha customers exist. |
| 4 | Variant comparison UI: side-by-side panel vs SQL filter? | **SQL filter via Grafana variable** on the `variant` column. | Zero custom code, leverages Grafana's built-in dropdown variables. UI work is YAGNI. |

## 3. Architecture Decisions

- **Service host**: `klai-knowledge-ingest` — reuses existing Procrastinate worker + asyncpg pool, no new service container.
- **Storage**: new table `rag_eval_results` in the existing `klai` Postgres database, schema verbatim from SPEC §Scope. Schema-prefix: `knowledge.rag_eval_results` (consistent with sibling tables `knowledge.artifacts`, `knowledge.crawled_pages`, etc.).
- **Migration tool**: plain SQL in `deploy/postgres/migrations/NNN_*.sql` (NO alembic — knowledge-ingest uses raw asyncpg + idempotent `CREATE TABLE IF NOT EXISTS`). Next number: `014` (last existing is `013_artifact_images.sql`). Manual deploy on core-01: `docker exec klai-core-postgres-1 psql -U postgres -d klai -f /docker-entrypoint-initdb.d/migrations/014_rag_eval_results.sql`.
- **Worker integration**: new Procrastinate task `evaluate_retrieval_quality_nightly` registered in `klai-knowledge-ingest/knowledge_ingest/worker.py`.
- **Schedule**: 02:00 UTC nightly, configured in the existing Procrastinate scheduling block.
- **Retrieval path**: harness calls the production `klai-retrieval-api` `/retrieve` endpoint over the Docker internal network using `KLAI_INTERNAL_SECRET` — same auth pattern as `deploy/litellm/klai_knowledge.py`. No bypass, no shortcut.
- **Judge LLM**: `klai-fast` via the LiteLLM proxy. Used for both the model answer (so `faithfulness` / `answer_relevance` reflect production behaviour) and the four RAGAS metric evaluations.
- **Suite location**: `klai-knowledge-ingest/knowledge_ingest/eval/suites/{chat,knowledge_org}.yaml`. Committed to git, reviewed by Mark before merge. `knowledge_personal.yaml` deferred — Voys has no personal-KB content; revisit when alpha customers create personal KBs.
- **Multi-tenant design**: each query in a suite YAML carries its own `org_zitadel_id`. v1 ships Voys-only, but the schema is already generic — adding a tenant post-launch is a pure YAML drop-in.
- **Test tenant**: Voys (`org_id = 368884765035593759`, plan `professional`). Single KB `support` with 501 active artifacts (422 kb_articles + 79 notion_pages).
- **Concurrency lock**: Procrastinate `queue_lock` keyed on suite name — at most one run per suite at a time.
- **Variant routing**: env var `RAG_EVAL_VARIANT` default `baseline`, written verbatim to every row.
- **Cost**: ~€5/month actual judge cost (recomputed; SPEC text needs correction at /run start).
- **Logging**: structlog → Alloy → VictoriaLogs. Each run emits `rag_eval_run_started`, per-query `rag_eval_query_evaluated`, and `rag_eval_run_completed` events.

## 4. Implementation Units

Five units, executed in dependency order. Each is a separate commit (or commit cluster) in the worktree.

### Unit 1 — DB schema (plain SQL migration)

- **Scope**: create `knowledge.rag_eval_results` table and the two indexes from SPEC §Scope. Plain SQL, idempotent (`CREATE TABLE IF NOT EXISTS`).
- **Files touched**:
  - new: `deploy/postgres/migrations/014_rag_eval_results.sql`
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/store.py` — asyncpg helper functions (`insert_eval_row`, `get_pool` reuse from existing `pg_store`).
- **Schema** (verbatim from SPEC):
  - `id BIGSERIAL PK`
  - `run_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `suite TEXT NOT NULL`
  - `variant TEXT NOT NULL DEFAULT 'baseline'`
  - `query_id TEXT NOT NULL`
  - `context_precision FLOAT, context_recall FLOAT, faithfulness FLOAT, answer_relevance FLOAT` (all nullable)
  - `retrieved_chunk_ids TEXT[]`
  - `retrieval_ms INT`
  - `total_tokens INT`
  - `meta JSONB`
- **Indexes**: `ix_rag_eval_run_at_suite (run_at DESC, suite)`, `ix_rag_eval_variant_run_at (variant, run_at DESC)`.
- **Acceptance test**: pytest fixture spins up a test DB, applies `014_rag_eval_results.sql` directly via asyncpg, asserts table + indexes exist via `pg_indexes` query, asserts `insert_eval_row()` round-trips a row.
- **EARS coverage**: REQ-2 (storage shape).
- **Dependencies**: none (root unit).

### Unit 2 — Harness skeleton + RAGAS install + Procrastinate task wiring

- **Scope**: install RAGAS, create `eval/` module, register the nightly Procrastinate task, wire concurrency lock and `RAG_EVAL_VARIANT` env var. No retrieval logic yet — just the orchestration shell.
- **Files touched**:
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/__init__.py`
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` (skeleton: entry function, lock, env var, structured logging)
  - modify: `klai-knowledge-ingest/pyproject.toml` — add `ragas` (pin a specific version after install-footprint check).
  - modify: `klai-knowledge-ingest/knowledge_ingest/worker.py` — register `evaluate_retrieval_quality_nightly` task and the cron schedule.
- **Pre-flight check** (DONE before /run): RAGAS install footprint measured at **403 MB** in a clean venv. Big-ticket transitive deps: scipy (90 MB), pyarrow (83 MB), numpy (41 MB), pandas (37 MB), sknetwork + PIL + langchain. No torch/transformers. Decision: reuse `klai-knowledge-ingest`. Pin version after install; freeze in `pyproject.toml`.
- **Concurrency**: Procrastinate `queue_lock=f"rag-eval-{suite}"` — second submit raises `RuntimeError`.
- **Variant**: `os.getenv("RAG_EVAL_VARIANT", "baseline")` read once at task entry, threaded through to every row write.
- **Logging**: emit `rag_eval_run_started` (suite, variant, query_count) and `rag_eval_run_completed` (suite, variant, rows_written, duration_ms).
- **Acceptance test**: pytest with Procrastinate's `InMemoryConnector` test runner — submit task twice in parallel, assert second submit raises `RuntimeError`.
- **EARS coverage**: REQ-5 (parallel guard), REQ-6 (variant tagging via env var).
- **Dependencies**: Unit 1 (schema must exist for the row writes Unit 3 will add).

### Unit 3 — Suite YAML loader + retrieval wrapper + RAGAS metrics

- **Scope**: load suite YAMLs, call retrieval-api with internal-secret auth, generate model answer via klai-fast, run RAGAS metrics, persist rows.
- **Files touched**:
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/suite_loader.py` — YAML parsing + schema validation (suite name, queries[].id, .query, .expected_topics, optional .expected_chunks, .org_zitadel_id, .user_zitadel_id).
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/retrieval_client.py` — `httpx.AsyncClient` wrapper that calls `http://klai-retrieval-api:8000/retrieve` with `Authorization: Bearer ${KLAI_INTERNAL_SECRET}` header and a 10s timeout.
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py` — wrapper that calls klai-fast via LiteLLM proxy for (a) generating the model answer and (b) RAGAS judge prompts.
  - modify: `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py` — fill in the skeleton from Unit 2.
- **Per-query flow**:
  1. Call retrieval-api `/retrieve` with the query and tenant headers. On HTTP error or timeout >10s, write a row with NULL metrics + `meta.error = "retrieval_failed: <reason>"` and continue (REQ-3).
  2. Generate a model answer via klai-fast using the retrieved chunks as context.
  3. Compute the four RAGAS metrics with klai-fast as judge LLM. Per-judge timeout 30s; on judge failure, leave that one metric NULL but persist the row.
  4. Write one row to `rag_eval_results` with all metrics, retrieved chunk IDs, retrieval_ms, total_tokens, and `meta = {"variant": ..., "kb_artifact_count": ..., "errors": [...]}`.
- **Acceptance tests**:
  - integration test against `httpx.MockTransport` returning canned chunks — verify all four metrics computed and one row written per query.
  - retrieval-failure test: mock returns HTTP 500 → row inserted with NULL metrics and `meta.error` populated (REQ-3).
- **EARS coverage**: REQ-1 (loads suites + runs production retrieval path), REQ-2 (writes row per query), REQ-3 (failure handling).
- **Dependencies**: Unit 2 (skeleton + Procrastinate registration).

### Unit 4 — Seed query generation procedure (60 queries, Voys-only)

- **Scope**: deliver an operator-runnable script that generates two suite YAMLs from Voys KB content. The script is run once during /run by Claude (Mark reviews the output before commit). The `knowledge_personal` suite is **explicitly deferred** to a follow-up SPEC once alpha customers have personal-KB content.
- **Files touched**:
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/generate_voys_seed_queries.py`
  - new (after script run): `eval/suites/chat.yaml`, `eval/suites/knowledge_org.yaml`
- **Generation procedure** (encoded in the script):
  1. Pull a random sample of 50 artifact paths from the Voys `support` KB, weighted by type (kb_articles 80%, notion_pages 20% to match prod ratio).
  2. For each sampled artifact, fetch the first 500 chars of artifact text as a topic seed.
  3. Cluster sampled artifacts by theme: CRM-integrations (Promedico, Efficy, Twinq, Resale Partners), telefonie-software (Yealink, Tinkle, Tring, 3CX), troubleshoot guides (Bubble, browser plugins), customer-service procedures (uitportering, fraude, account changes, openingstijden).
  4. Generate 30 queries per suite with the following mix:
     - 30% easy-lookup (regression canaries — should always score ≈1.0 and have `expected_chunks` populated)
     - 30% vague-pronoun ("die klant", "deze functie" — query-rewrite targets)
     - 20% multi-doc synthesis (parent-child + GraphRAG targets)
     - 15% long-tail / obscure (recall test)
     - 5% edge cases (empty, mixed-language, malformed)
  5. Annotate every query with `expected_topics: list[str]` (LLM-judge guidance) and, for the easy-lookup canaries only, `expected_chunks: list[str]` (gold-standard chunk paths).
  6. Output two YAML files into `eval/suites/`. Each query carries `org_zitadel_id: "368884765035593759"` (Voys).
- **Suite-specific notes**:
  - `chat.yaml` — conversational style against the support KB, e.g. "Wat doe ik als een klant z'n inloggegevens kwijt is?"
  - `knowledge_org.yaml` — direct-search style against the support KB, e.g. "Welke Yealink firmware ondersteunen we?", "Hoe troubleshoot ik Bubble?"
- **Review gate**: Mark reviews the generated YAMLs before commit. The script does NOT auto-commit.
- **EARS coverage**: none directly; REQ-7 needs at least one suite to exist on disk.
- **Dependencies**: Unit 3 (the harness must be runnable before suites are useful).

### Unit 5 — Grafana panel + alert + ad-hoc CLI

- **Scope**: visualise the metrics, alert on regressions, expose the ad-hoc operator entrypoint required by REQ-7.
- **Files touched**:
  - new: `klai-infra/grafana/dashboards/rag-quality.json` — PostgreSQL datasource panel "RAG quality (24h baseline)" with 4 time-series per suite (one per metric), 7-day moving average, Grafana variable `$variant` for the SQL filter.
  - new: `klai-infra/grafana/alerts/rag-quality-faithfulness.yaml` — alert rule firing when `faithfulness < 0.85` for two consecutive runs (REQ-4 reference). Notification channel: existing on-call route.
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/__main__.py` — argparse-based CLI: `python -m knowledge_ingest.eval.ragas_runner --suite chat --variant my-experiment` (REQ-7). Mirrors the Procrastinate task's logic but runs synchronously and prints results.
- **Panel SQL skeleton** (pseudo, not committed):
  - `SELECT time_bucket('1d', run_at) AS time, AVG(<metric>) FROM rag_eval_results WHERE suite = '$suite' AND variant = '$variant' AND run_at > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY 1`
  - 7-day moving-average computed in Grafana via a transform.
- **Alert evaluation**: query the latest two runs per suite where `variant = 'baseline'`; fire if both have `faithfulness < 0.85`.
- **Acceptance tests**:
  - dashboard JSON syntactically valid (`jq . rag-quality.json` succeeds).
  - CLI smoke test: `python -m knowledge_ingest.eval.ragas_runner --suite chat --variant smoke-test` finishes within 5 minutes against the 30-query `chat` suite (REQ-7).
- **EARS coverage**: REQ-4 (Grafana 7-day moving average), REQ-7 (ad-hoc CLI).
- **Dependencies**: Unit 3 (rows must be writable).

## 5. Risks and Mitigations

- ~~RAGAS install footprint > 200 MB.~~ **RESOLVED**: measured at 403 MB pre-/run. Scientific-stack libs only, no GPU bloat. Reuse `klai-knowledge-ingest` confirmed. Docker image grows ~300 MB; one-time pull cost acceptable for a nightly job.
- **klai-fast availability via LiteLLM.** Harness depends on the proxy. Mitigation: 30s per-judge-call timeout; on failure, leave that one metric NULL and persist the row anyway. Don't crash the run on a single judge failure.
- **Concurrent runs against the same suite.** Mitigated by Procrastinate `queue_lock=f"rag-eval-{suite}"` (REQ-5).
- **Voys KB drift.** As the KB grows, baseline metrics will shift even without retrieval changes. Mitigation: every row's `meta` includes `kb_artifact_count` snapshot so score changes can be correlated with corpus changes.
- **No personal-KB data.** `knowledge_personal.yaml` ships with 10 synthetic queries in v1. Document as known limitation; backfill once alpha customers create personal KBs.
- **Judge-LLM scoring drift.** Mistral small can be inconsistent across runs. Mitigation: run nightly so variance averages out over 7 days. If individual-night variance exceeds 0.1 standard deviation on `baseline` metrics, escalate the model choice (consider klai-pipeline) in a follow-up SPEC.
- **Cost overrun.** Estimate is ~€5/month. Mitigation: log `total_tokens` per row; if monthly aggregate exceeds €15 (3× budget), pause the cron and re-evaluate.

## 6. Sequencing

```
Unit 1 (schema)
   ↓
Unit 2 (skeleton + Procrastinate)
   ↓
Unit 3 (loader + retrieval + RAGAS)
   ↓
   ├─→ Unit 4 (seed queries) ─┐
   └─→ Unit 5 (Grafana + CLI) ─┴─→ done
```

- Unit 1 → Unit 2: skeleton needs the table to write to.
- Unit 2 → Unit 3: loader and retrieval wrapper need the registered task and lock.
- Unit 3 → Unit 4: queries are useless without a harness to run them.
- Unit 3 → Unit 5: Grafana needs rows in the table; CLI exercises the same code path as Unit 3.
- Unit 4 ↔ Unit 5: parallel after Unit 3.

## 7. TDD Plan

| Unit | RED (failing test first) | GREEN (minimum to pass) | REFACTOR opportunities |
|------|--------------------------|--------------------------|--------------------------|
| 1 | pytest characterization SQL test: assert `rag_eval_results` table exists + both indexes match SPEC column order. | Apply alembic migration. | Extract index DDL into a shared helper if a future SPEC adds a third index. |
| 2 | pytest with Procrastinate `InMemoryConnector`: submit task, assert it runs end-to-end (no-op body); submit twice in parallel, assert second submit raises `RuntimeError`. Also: assert `RAG_EVAL_VARIANT` env var threads through to a debug hook. | Register task with `queue_lock`, read env var. | Extract lock-key formatter into utility if more queue-locked tasks land. |
| 3 | (a) integration test with `httpx.MockTransport` returning canned chunks → assert one row written per query with all four metrics non-null. (b) retrieval-failure test: mock returns HTTP 500 → assert row inserted with NULL metrics + `meta.error` (REQ-3). (c) judge-failure test: judge call raises → assert row inserted with that one metric NULL but others present. | Implement loader, retrieval client, judge client, persist row. | Pull `httpx.AsyncClient` construction into a fixture; deduplicate retry logic between retrieval-client and judge-client. |
| 4 | Not TDD-style. Manual review by Mark after generation. Optional: lightweight schema validation test on the YAML output (`pytest` parses each suite, asserts queries[].id is unique, expected_topics non-empty). | Run script, hand-curate, commit YAMLs. | n/a |
| 5 | (a) `jq . rag-quality.json` exits 0. (b) CLI smoke test: `python -m knowledge_ingest.eval.ragas_runner --suite chat --variant smoke-test` exits 0 and writes ≥1 row within 5 min (REQ-7). | Build dashboard JSON, alert YAML, `__main__.py`. | If a third entrypoint emerges (e.g. a /admin trigger), extract shared CLI scaffolding into a click/typer command group. |

## 8. Acceptance Criteria Mapping

| EARS REQ | Unit |
|----------|------|
| REQ-1 (loads suites + runs production retrieval path) | Unit 3 |
| REQ-2 (writes one row per query with all metrics) | Unit 1 (schema) + Unit 3 (writes) |
| REQ-3 (retrieval failure → NULL metrics + meta.error) | Unit 3 |
| REQ-4 (Grafana 7-day moving average) | Unit 5 |
| REQ-5 (parallel runs raise RuntimeError) | Unit 2 |
| REQ-6 (variant via env var, default baseline) | Unit 2 |
| REQ-7 (ad-hoc CLI returns 30-query result within 5 min) | Unit 5 (with code from Unit 3) |

## 9. SPEC Update Required Before /run

The SPEC's Open Question #2 estimates LLM-as-judge cost at ~€54/month based on "~€0.01 per 1k tokens". That number is wrong by an order of magnitude:

- Actual klai-fast pricing (Mistral small via LiteLLM): ~€0.60 per **million** input tokens, ~€1.80 per million output tokens.
- 10,800 calls × 500 input tokens = 5.4M input tokens → €3.24
- 10,800 calls × 50 output tokens = 0.54M output tokens → €0.97
- Subtotal **€4.21/month**, plus retry/buffer ≈ **€5/month**.

**Action**: at the start of /run, the first commit corrects `spec.md` Open Question #2 with the recomputed math and marks the question as resolved (cost is acceptable, ~€5/month, not ~€54). All other open-question resolutions from §2 of this plan also land in the same `spec.md` update commit.
