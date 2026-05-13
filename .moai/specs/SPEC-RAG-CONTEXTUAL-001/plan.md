# Plan: SPEC-RAG-CONTEXTUAL-001 — Anthropic-pattern Contextual Retrieval

## 1. Overview

This SPEC evolves the existing chunk enrichment pipeline so that the per-chunk `context_prefix` is grounded in an LLM-generated **document summary**, not a truncated slice of raw document text. The current pipeline (`klai-knowledge-ingest/knowledge_ingest/enrichment.py::enrich_chunks`) already implements the structural shape of Anthropic's contextual retrieval — it generates a `context_prefix` per chunk, prepends it to chunk text, and embeds the result on both dense and sparse vectors. What it does **not** yet do is:

1. Generate a document-level summary once per artifact and reuse it across all chunks of that document. Today the prompt receives a context window from `context_strategies.STRATEGIES` (first-N tokens, rolling-window, most-recent, or front-matter) — a heuristic snippet of raw text, not a summary.
2. Cache the document summary so re-ingest of the same content is free.
3. Provide an operator-triggered backfill path to recontextualise an existing KB once the summary-driven prompt is wired up.
4. Tag the result with an eval variant so SPEC-RAG-EVAL-001 can measure delta against the captured Voys baseline (`context_precision=0.25`, `context_recall=0.26`, `retrieval_ms=542`).

The work is the highest-ROI Tier-1 enhancement after EVAL-001 because Anthropic's published delta (-49% retrieval-failure-rate) was measured precisely against the gap this plan closes — context generated from a *summary* of the whole doc, not a window. We get to skip the wholesale Qdrant payload rewrite that the SPEC's "in scope" section implies, because `context_prefix` and `text_enriched` are already in the payload (`klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:267-274`). The work is concentrated in `enrichment.py` (prompt evolution + summary plumbing), one new module for summary generation + caching, one new Procrastinate task for backfill, and an eval-variant verification step.

## 2. Resolved open questions

| # | SPEC question | Resolution | Rationale |
|---|---|---|---|
| 1 | Document summary per ingest-batch or per artifact? | **Per artifact, cached on `knowledge.artifacts.extra.document_summary`, keyed by `content_hash` already on the artifact row.** Multi-batch ingests (long Notion page split across chunks) reuse the cached value. Re-ingest with unchanged `content_hash` is a free read. | Artifacts already carry `content_hash` (see `pg_store.get_active_content_hash`). The `extra` JSONB column already merges via `update_artifact_extra` (`pg_store.py:776`). No schema migration. |
| 2 | Prompt caching with Mistral via LiteLLM? | **No prompt-caching dependency.** LiteLLM proxy does not expose Anthropic-style prompt caching for Mistral. Each chunk-context call sends the full (summary + chunk + instructions). The cost analysis in §3 shows this is acceptable: Voys whole-corpus recontextualisation costs ~€2 once. | Avoids a multi-week proxy-side feature dependency. Cost is low enough that the simplification is the right trade. |
| 3 | Sparse vs dense — prepend context to both? | **Both. The current pipeline already does this** (`enrichment_tasks.py:315-329` — same `enriched_texts` is fed to dense embedder and sparse sidecar). No new decision; we keep parity with Anthropic's "Contextual BM25" and confirm that the longer input still fits BGE-M3-sparse's 8192-token max input. With document_summary (≤120 tokens) + chunk (~400 tokens) + chunk_text the typical input is ~600 tokens, well under the limit. | Existing behaviour is correct; we only verify limits with an integration test. |
| 4 | Backward compatibility for legacy chunks? | **Already handled by the existing read path.** `klai-retrieval-api/retrieval_api/services/search.py` already reads `payload.get("context_prefix")` with `None` fallback (lines 211, 313, 384). New write-side change keeps emitting `context_prefix`; recontextualisation only updates the value, not the schema. | No payload schema change required. Legacy chunks just have a "weaker" context_prefix until the operator runs `recontextualize_kb`. |

The SPEC's claim "every chunk SHALL have a `chunk_context` field" is interpreted in this plan as "every chunk SHALL continue to have a `context_prefix` field" — keeping the existing payload key avoids a Qdrant payload migration and a coordinated read-side rename in retrieval-api. The eval-variant tag (`contextual_v1`) marks the boundary between the heuristic-window era and the summary-driven era; rows in `knowledge.rag_eval_results` with `variant='contextual_v1'` reflect the new prompt.

## 3. Cost / footprint analysis

klai-fast pricing (Mistral small via LiteLLM proxy):
- Input: ~€0.60 per million tokens.
- Output: ~€1.80 per million tokens.

Per chunk-context call (already in production for ~501 Voys artifacts, ~6 chunks/artifact average):
- Prompt = (document_summary 120 tokens) + (chunk text ~400 tokens) + (system instructions ~80 tokens) ≈ **600 input tokens**.
- Response: 1 sentence, ≤30 words ≈ **50 output tokens**.
- Cost per chunk-context: 600 × €0.60/M + 50 × €1.80/M = €0.00036 + €0.00009 = **€0.00045 ≈ 4.5 × 10⁻⁴ €/chunk**.

Per document-summary call (new, once per artifact):
- Prompt = (document text up to 4000 tokens) + (system instructions ~50 tokens) ≈ **4050 input tokens**.
- Response: ≤120 tokens.
- Cost per summary: 4050 × €0.60/M + 120 × €1.80/M = €0.00243 + €0.00022 = **€0.00265 ≈ 2.7 × 10⁻³ €/artifact**.

Per 8000-token document (REQ-6 cap = €0.05):
- 1 document-summary call: ~€0.0027.
- 20 chunk-context calls (~400 tokens each): 20 × €0.00045 = €0.009.
- Total per 8000-token doc: ~€0.012, **well under €0.05 cap**. REQ-6 verified mathematically; runtime verification happens in Unit 5.

For Voys whole-corpus recontextualisation (501 artifacts × ~6 chunks each ≈ 3000 chunks; artifact text averaging ~3000 tokens):
- 501 summaries × €0.0027 ≈ **€1.35**.
- 3000 chunk-contexts × €0.00045 ≈ **€1.35**.
- One-time total: **~€2.70**, plus ~20% safety buffer → **< €4 for full Voys backfill**. Negligible.

LiteLLM proxy load: 3000 chunk-context calls + 501 summary calls = ~3500 calls. With concurrency cap 4 and ~5s per call, full Voys recontextualisation runs in **~75 minutes** (3500 / 4 × 5s ≈ 73 min). Acceptable for an operator-triggered backfill; Procrastinate handles retry on individual failures. Bound concurrency to 4 to avoid stampeding the Mistral upstream rate limit.

Embedding cost overhead: each chunk's embedding input grows by ~120 tokens (the summary length). With Voys at ~3000 chunks, the recurring TEI/sparse cost increase is negligible (TEI is local on gpu-01 — no per-token cost; sparse sidecar is local). Network tax only.

## 4. Architecture decisions

- **Document summary lives on `knowledge.artifacts.extra.document_summary` (JSONB key).** Reuse of the existing extra column means **no schema migration required**. This is the smallest possible addition — `update_artifact_extra(artifact_id, {"document_summary": "..."})` exactly fits the current API.
- **Cache key for the summary is `content_hash`** (already populated on every artifact row). Re-ingest with unchanged content reads the existing `extra.document_summary`. Re-ingest with changed content unsets the field (forces regeneration).
- **`klai-fast` (Mistral small via LiteLLM)** for both summary and chunk-context generation. Same proxy auth, same `litellm_url` + `litellm_api_key` settings already used by `enrichment.py`.
- **The chunk-context prompt evolves**: today it receives a `document_text` window via `context_strategies`. The new prompt receives `document_summary` (1-2 sentences). When a summary is unavailable (legacy artifact + first ingest before summary cache populated), the existing window-based path remains as a fallback so the pipeline never blocks on missing summaries (REQ-2).
- **`context_prefix` payload key in Qdrant is unchanged.** No payload migration; retrieval-api keeps reading `payload.get("context_prefix")` with the same backward-compatible None fallback it already has.
- **`embedding_input` shape is unchanged.** `enrichment.enrich_chunks` already produces `enriched_text = "{context_prefix}\n\n{original_text}"` and feeds it identically to dense and sparse embedders. REQ-3 satisfied without code change at the embedder call sites.
- **Reranker call site in `klai-retrieval-api/retrieval_api/services/reranker.py` is OUT OF SCOPE for this PR.** Today the reranker receives `chunk.text` (the raw text). The "Anthropic full pattern" wants reranker to also see the prepended context. Coordination flagged in §10; tracked as a separate follow-up.
- **Operator-triggered backfill via new Procrastinate task `recontextualize_kb(org_id, kb_slug)`.** Lands on the `RAG_EVAL` LLM lane (already provisioned in `queues.py:60`) — same lane as the eval harness so we don't create a new queue. Reuses the rate-limited LLM lane.
- **Idempotency** by content_hash: the task skips artifacts where `extra.document_summary` already corresponds to the current `content_hash`. A `--force` mode regenerates everything (used when prompt changes).
- **Eval variant tag** `RAG_EVAL_VARIANT=contextual_v1`. The harness already accepts this env var and writes it to `knowledge.rag_eval_results.variant`.
- **No alembic.** Plain SQL migration if required (it is NOT required — we reuse `extra` JSONB). The "next migration is `015_*.sql`" path stays available for any future column-level addition that this SPEC's measurement reveals as necessary.
- **Concurrency** for the backfill task: bounded by `asyncio.Semaphore(4)` to stay below the Mistral upstream rate limit (existing `settings.enrichment_max_concurrent` pattern reused).
- **Logging.** New structured events: `document_summary_generated`, `document_summary_cache_hit`, `recontextualize_kb_started`, `recontextualize_kb_artifact_processed`, `recontextualize_kb_completed`, `recontextualize_kb_failed`. Same structlog → Alloy → VictoriaLogs path as existing enrichment events.

## 5. Implementation units (5)

Five units, executed in dependency order. Each is a separate commit cluster in the worktree.

### Unit 1 — Document summary generator + cache lookup

- **Scope.** New module that produces a 1-2 sentence summary of an artifact's full text via klai-fast, with content_hash-keyed cache lookup against `knowledge.artifacts.extra.document_summary`.
- **Files touched.**
  - new: `klai-knowledge-ingest/knowledge_ingest/contextual.py`
  - modify: `klai-knowledge-ingest/knowledge_ingest/pg_store.py` (one read helper `get_artifact_summary(artifact_id) -> tuple[str | None, str | None]` returning `(summary, content_hash)`).
- **API.**
  - `async def generate_document_summary(text: str, title: str, kb_name: str, llm_client) -> str` — returns ≤120 token summary. On LLM failure: returns `""` (caller treats empty as "no summary, fall back to context_strategies window").
  - `async def get_or_generate_document_summary(artifact_id: str, content_hash: str, text: str, title: str, kb_name: str) -> str` — cache-aware wrapper. Reads `pg_store.get_artifact_summary(artifact_id)`; if cached `summary` exists AND its associated `content_hash` matches the live `content_hash`, returns cached. Otherwise calls `generate_document_summary`, persists via `pg_store.update_artifact_extra(artifact_id, {"document_summary": summary, "document_summary_content_hash": content_hash})`, returns the new value.
- **Prompt template** (Dutch + English mix, matching enrichment.py style):
  - `Document title: {title}\nKnowledge base: {kb_name}\n\nDocument text (first 4000 tokens):\n<document>\n{document_text_truncated}\n</document>\n\nWrite a 1-2 sentence summary (≤120 tokens) describing what this document is, what topic it covers, and which audience or scenario it addresses. Reply with ONLY the summary text, no preamble.`
  - Truncation: `_truncate_to_tokens(text, 4000)` reusing existing helper in `enrichment.py`.
- **Failure handling.** 30s timeout per call (mirror `settings.enrichment_timeout`). On HTTP/timeout failure: log `document_summary_failed`, return `""`. On parse error: same.
- **Tests.**
  - unit: mock LiteLLM via `httpx.MockTransport` returning canned summary → assert ≤120 tokens, persists to `extra.document_summary`, second call hits cache.
  - unit: mock LiteLLM returning HTTP 500 → assert returns `""`, no exception, warning logged.
  - unit: cache invalidation when content_hash changes → second call regenerates.
- **EARS coverage.** Foundation for REQ-1 (chunks need a non-trivial chunk_context, which requires a summary), REQ-4 (idempotency by content_hash). Direct coverage of failure path REQ-2.
- **Dependencies.** None (root unit).

### Unit 2 — Wire summary-driven prompt into existing enrichment

- **Scope.** Modify `enrichment.enrich_chunks` so the per-chunk LLM call receives `document_summary` instead of (or in addition to) the truncated `context_window`. Keep the `context_strategies` window as a documented fallback when the summary is empty (REQ-2 robustness).
- **Files touched.**
  - modify: `klai-knowledge-ingest/knowledge_ingest/enrichment.py`
  - modify: `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` — call `contextual.get_or_generate_document_summary` BEFORE `enrichment.enrich_chunks` in `_enrich_document` (currently around line 287). Pass the summary into `enrich_chunks` via a new optional kwarg.
  - modify: `klai-knowledge-ingest/knowledge_ingest/enrichment.py::ENRICHMENT_PROMPT` — accept a new `document_summary` slot. When summary is empty/missing, fall back to existing `<document>{document_text}</document>` window (gracefully degrades, no behaviour regression for legacy callers).
- **Prompt evolution.** Add a `<document_summary>` block above the existing `<document>` window. The LLM is instructed to use the summary when present, the window otherwise. Both blocks coexist for two ingests-cycles to give us empirical comparison; eval variant `contextual_v1` denotes the moment the summary block is non-empty. The `<document>` window stays in place for the legacy fallback path.
- **Tests.**
  - integration: in-memory `_enrich_document` runner with `httpx.MockTransport` LLM. Verify (a) summary generation called once per artifact, (b) `enrich_chunks` receives the summary, (c) `context_prefix` returned by mocked LLM lands in Qdrant payload via existing `upsert_enriched_chunks` path.
  - integration: artifact missing `extra.document_summary` AND summary generation fails → `enrich_chunks` falls back to `context_window`-only prompt; pipeline still produces a `context_prefix` (REQ-2).
  - integration: re-ingest of same artifact with unchanged `content_hash` → summary cache-hit, no new LLM call for summary (REQ-4).
- **EARS coverage.** REQ-1 (chunk_context field populated post-ingest), REQ-2 (failure handling), REQ-3 (embedding_input shape — unchanged but verified).
- **Dependencies.** Unit 1 (summary generator must exist).

### Unit 3 — Verify Qdrant + sparse/dense parity for the longer input

- **Scope.** No code changes expected — the existing `enrichment_tasks._enrich_document` already feeds `enriched_texts` to both dense (TEI) and sparse (BGE-M3 sidecar) embedders. This unit is **defensive verification**: confirm the longer summary-driven `enriched_text` doesn't blow past the BGE-M3 input limit, doesn't change the Qdrant payload schema, and doesn't regress `text` / `text_enriched` / `context_prefix` payload keys.
- **Files touched.**
  - none (verification-only) UNLESS the verification reveals a token-limit issue, in which case `_truncate_to_tokens` is applied to `enriched_text` before sparse embedding.
- **Tests.**
  - unit: synthetic `EnrichedChunk` with `enriched_text` length ~6000 tokens → `embed_sparse_batch` returns a non-None sparse vector.
  - unit: Qdrant payload assembly via `qdrant_store.upsert_enriched_chunks` includes both `text` (original) and `text_enriched` (with prepended context) and `context_prefix` (the summary-driven sentence). No new payload keys required by SPEC.
- **EARS coverage.** REQ-1 (Qdrant payload shape), Open Question 3 (sparse + dense both prepended — already true).
- **Dependencies.** Unit 2 (summary-driven `enriched_text` must be reachable to verify against).

### Unit 4 — Operator-triggered re-contextualisation Procrastinate task

- **Scope.** New Procrastinate task that iterates artifacts in a KB, regenerates summary + per-chunk context using the new prompt, re-embeds all chunks, and overwrites Qdrant payload. Idempotent (cache by content_hash). Operator-triggered, not scheduled.
- **Files touched.**
  - new: `klai-knowledge-ingest/knowledge_ingest/recontextualize_tasks.py` exposing `register_recontextualize_tasks(procrastinate_app)` and the task body `recontextualize_kb(org_id: str, kb_slug: str, force: bool = False) -> dict`.
  - modify: `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py::init_app` — add `register_recontextualize_tasks(_procrastinate_app)` next to the other `register_*_tasks` calls.
  - modify: `klai-knowledge-ingest/knowledge_ingest/queues.py` — no new queue; reuse `RAG_EVAL` (LLM lane). The constant comment is updated to mention contextual backfill.
  - new: `klai-knowledge-ingest/knowledge_ingest/eval/__main__.py` — extend the existing CLI module to accept a `--recontextualize-kb` subcommand, OR create a small new entry point `python -m knowledge_ingest.recontextualize --org-id X --kb-slug Y`. Decision: separate entrypoint for orthogonal concerns.
  - new: `docs/runbooks/recontextualize-kb.md` — operator-facing runbook (trigger, monitor via Procrastinate dashboard, rollback by reverting commit + restarting workers; no Qdrant rollback path, see §6 risks).
- **Task body.**
  1. Fetch all active artifacts for `(org_id, kb_slug)` via `pg_store` (a new helper `iter_active_artifacts(org_id, kb_slug)` returning `(artifact_id, content_hash, title, document_text, extra)` rows; consult `pg_store.py` for the existing query patterns and add this helper).
  2. For each artifact: call `contextual.get_or_generate_document_summary` (force-regenerates when `force=True`).
  3. Re-chunk only when `force=True` AND `extra.chunker_version` differs (defer chunker re-versioning to a later SPEC; keep `force` semantics narrow to "regenerate summary + chunk_context, reuse existing chunk boundaries").
  4. Call `enrichment.enrich_chunks` with the new summary in the kwarg path.
  5. Re-embed via existing `embedder.embed` + `sparse_embedder.embed_sparse_batch` + (conditionally) `embedder.embed` for questions.
  6. Call `qdrant_store.upsert_enriched_chunks` (same function the live ingest uses) — overwrite is idempotent because `upsert_enriched_chunks` deletes existing points for the path before insert.
  7. Bounded concurrency: `asyncio.Semaphore(4)` over the artifact loop.
  8. Emit per-artifact and aggregate structured logs.
- **Tests.**
  - end-to-end with stub Qdrant + stub LiteLLM: 5 artifacts × 3 chunks each → after task, all 5 documents have `extra.document_summary` populated, all 15 chunks have a non-trivial `context_prefix`, `text_enriched` includes the prepended prefix.
  - idempotency: re-running the task on unchanged content (no `--force`) is a no-op (zero LLM calls); `--force=True` regenerates everything.
  - failure-mid-run: artifact 3's LLM call fails → artifacts 1, 2 succeed and are persisted; artifact 3 logged with `recontextualize_kb_artifact_failed`; artifacts 4, 5 still process; aggregate log records `failed=1, succeeded=4`. Procrastinate retry kicks in on the whole task only when more than half the artifacts fail (otherwise we get retry storms).
- **EARS coverage.** REQ-4 (idempotency).
- **Dependencies.** Units 1, 2, 3 (needs the generators + the wired pipeline + the verified embedding path).

### Unit 5 — Eval variant verification + uplift documentation

- **Scope.** Run the SPEC-RAG-EVAL-001 harness against Voys with `RAG_EVAL_VARIANT=contextual_v1` after Unit 4 has recontextualised the KB. Verify ≥10% improvement in `context_precision` AND ≥5% improvement in `faithfulness` (REQ-5). Capture cost per recontextualisation (REQ-6).
- **Files touched.**
  - modify: `docs/architecture/retrieval-improvements-roadmap.md` — append a "Tier 1 results" section with the measured uplift, after the experiment runs successfully on staging.
  - modify: `.moai/specs/SPEC-RAG-CONTEXTUAL-001/spec.md` — `status: draft` → `status: completed` post-merge.
  - new: `docs/runbooks/recontextualize-kb.md` (created in Unit 4) gets an "Eval comparison" section linking to the Grafana panel filtered by `variant=contextual_v1`.
- **Procedure (operator-runnable).**
  1. Deploy the merged PR to staging.
  2. `docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.recontextualize --org-id 368884765035593759 --kb-slug support` (Voys).
  3. Wait for completion (~75 min per §3 estimate); Procrastinate dashboard tracks progress.
  4. Capture LiteLLM token usage during the window: query Grafana / VictoriaLogs for `service:litellm AND ts:[start, end]` to count input/output tokens. Compute average per-doc cost and verify < €0.05 per 8000-token doc.
  5. Trigger eval: `docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.eval --suite chat --variant contextual_v1`.
  6. Compare via Grafana (RAG quality dashboard, `$variant` filter): `contextual_v1` vs `baseline` rows on `chat` and `knowledge_org` suites. Verify ≥10% precision uplift AND ≥5% faithfulness uplift on at least one suite.
  7. If thresholds met: deploy to production, run recontextualize_kb on production Voys.
  8. If thresholds not met: iterate prompt in `enrichment.py`, re-run on staging with new variant tag (`contextual_v2` etc.), repeat.
- **Acceptance.** No new tests in this unit; gate is the post-deploy comparison query plus the cost audit.
- **EARS coverage.** REQ-5 (post-deploy uplift), REQ-6 (cost cap).
- **Dependencies.** Units 1-4.

## 6. Risks and mitigations

- **klai-fast quality on Dutch content.** Voys's `chat` suite is Dutch; Mistral small is multilingual but has weaker Dutch coverage than English. Risk: `chat` precision uplift smaller than `knowledge_org` uplift.
  - Mitigation: monitor per-suite metrics. The eval harness writes one row per query so per-suite analysis is built-in. If `chat` uplift is < 5% but `knowledge_org` ≥ 10%, iterate the Dutch prompt before declaring success.
- **LiteLLM proxy bottleneck during whole-corpus backfill.** 3500 sequential calls could pummel the proxy and push other ingest jobs onto its retry path.
  - Mitigation: bounded concurrency 4, run during off-hours, use the LLM lane (which is already rate-limited by upstream Mistral). Operator runbook specifies "run after 22:00 CEST; expect ~75 min".
- **Document summary drift on content updates.** Re-ingesting a changed document doesn't automatically refresh the summary unless we explicitly invalidate. Risk: outdated summary persists until next manual `recontextualize_kb`.
  - Mitigation: when `content_hash` changes during ingest (visible in `pg_store.get_active_content_hash`), the new ingest path invalidates `extra.document_summary` (sets to None) and `extra.document_summary_content_hash` so the next chunk-context call regenerates. Encoded in Unit 2.
- **Qdrant payload migration risk.** None expected — we keep `context_prefix` and `text_enriched` payload keys exactly as today. The risk surfaces only if Unit 3 verification reveals a token-limit issue, in which case truncation lands as a defensive fix.
- **Embedding cost overrun.** Each chunk's `enriched_text` grows by ~120 tokens (the summary). TEI is local on gpu-01 (no per-token cost) and the sparse sidecar is local; only network/CPU tax. Negligible.
- **Recontextualize idempotency edge case.** Two operators running `recontextualize_kb` against the same `(org_id, kb_slug)` simultaneously could double-write Qdrant points.
  - Mitigation: Procrastinate `queueing_lock=f"recontextualize-{org_id}-{kb_slug}"` on the task; second invocation raises `RuntimeError`. Same lock pattern used by SPEC-RAG-EVAL-001.
- **Failure recovery during partial backfill.** If the task crashes mid-run (e.g. proxy outage), some artifacts have new summaries and some don't. Risk: partially recontextualised KB until next run.
  - Mitigation: idempotency (Unit 4 step 1) means re-running the task picks up where it left off — artifacts with current `document_summary_content_hash` matching live `content_hash` are skipped. Crash recovery is automatic.
- **Reranker sees stale text.** Out of scope for this SPEC, but worth tracking: the reranker today receives `chunk.text` (original). After this PR, embedding sees `chunk_context + text` but reranker still sees `text`. Potential mismatch in scoring signal.
  - Mitigation: §10 coordination note. Not blocking; the SPEC's REQ-3 explicitly carves the reranker out ("Reranker receives the full text plus context" — but the existing SPEC text says this; the existing reranker code does NOT yet do this; flag is in §10).

## 7. Sequencing

```
Unit 1 (summary generator + cache)
   ↓
Unit 2 (wire summary into enrichment prompt)
   ↓
Unit 3 (Qdrant + sparse/dense verification — defensive)
   ↓
Unit 4 (operator-triggered backfill task + runbook)
   ↓
Unit 5 (deploy + eval-variant verification + roadmap update)
```

- Unit 1 → Unit 2: prompt evolution depends on `get_or_generate_document_summary` being callable.
- Unit 2 → Unit 3: verification needs the summary-driven `enriched_text` to exercise.
- Units 1-3 → Unit 4: backfill task reuses all three building blocks.
- Units 1-4 → Unit 5: nothing to verify until the task and the prompt have shipped.

Units 1, 2, 3 land in a single PR (or ordered commits within one PR). Unit 4 lands as a follow-up PR (operator tooling is independently reviewable). Unit 5 is post-deploy verification, not a PR.

## 8. TDD plan per unit

| Unit | RED (failing test first) | GREEN (minimum to pass) | REFACTOR opportunities |
|---|---|---|---|
| 1 | (a) pytest with `httpx.MockTransport` returning canned summary → assert `generate_document_summary` returns the canned string and persists it via `update_artifact_extra`. (b) mock returns HTTP 500 → assert empty string returned, warning logged, no exception. (c) cache-hit test: pre-populate `extra.document_summary` and matching `document_summary_content_hash` → assert no HTTP call made. | Implement `contextual.py` with the three functions; one POST to LiteLLM; one UPDATE on `knowledge.artifacts.extra`. | Extract a generic `cached_llm_call(cache_key, generator_fn)` helper if a third caller (e.g. clustering proposal generator) wants the same cache pattern. |
| 2 | (a) integration test: `_enrich_document` end-to-end with a stubbed Qdrant client + `httpx.MockTransport` for both summary and chunk-context calls → assert summary called once per artifact, chunk-context called N times, `text_enriched` in Qdrant payload contains both summary-derived prefix and original chunk text. (b) summary fails → fallback to context_strategies window → chunk-context still produced. | Add `document_summary` kwarg threading from `_enrich_document` → `enrichment.enrich_chunks` → `enrich_chunk` → `ENRICHMENT_PROMPT.format`. | Unify the prompt-template ladder (currently 1 retry on chunk_type validation; add a similar retry for context_prefix length validation if needed). |
| 3 | unit test with synthetic 6000-token `enriched_text` → assert `embed_sparse_batch` returns non-None vector. Qdrant payload assembly test asserts `text`, `text_enriched`, `context_prefix` keys present. | No code change unless test reveals truncation needed; if needed, apply `_truncate_to_tokens(enriched_text, 4000)` before sparse embedding. | n/a (defensive unit). |
| 4 | (a) end-to-end with stub Qdrant + stub LiteLLM, 5 artifacts × 3 chunks → all summaries populated, all 15 chunks have new `context_prefix`. (b) idempotency: re-run with unchanged content → zero LLM calls. (c) `--force=True` → regenerates everything. (d) artifact-3 LLM failure → artifacts 1,2,4,5 still complete; aggregate log shows `failed=1, succeeded=4`. | Implement `recontextualize_tasks.py` task body; reuse `enrichment.enrich_chunks`, `embedder.embed`, `sparse_embedder.embed_sparse_batch`, `qdrant_store.upsert_enriched_chunks`. | Extract per-artifact processing into a private helper to dedupe with `_enrich_document`'s post-summary path. |
| 5 | n/a (verification step, not TDD). Acceptance is the Grafana comparison + cost-audit query. | n/a | If the eval comparison reveals systematic per-suite divergence, formalise the per-suite uplift threshold into the harness (e.g. fail the deploy gate when uplift < 5% on any suite). |

## 9. Acceptance criteria mapping

| EARS REQ (from spec.md) | Coverage | Unit |
|---|---|---|
| REQ-1: New ingests produce chunks with `chunk_context` (interpreted: `context_prefix`) of length 10-500 chars in Qdrant payload. | Unit 2 wires the summary-driven prompt; Unit 3 verifies payload shape. | 2 + 3 |
| REQ-2: Context generation failure → chunk still embedded with original text, `chunk_context: null`; pipeline never blocks. | Unit 1's `generate_document_summary` returns `""` on failure; Unit 2's fallback to context_strategies window keeps `enrich_chunk` working when summary is empty; existing `enrichment.py` already returns `context_prefix=""` on retry exhaustion. | 1 + 2 |
| REQ-3: `embedding_input = chunk_context + "\n\n" + chunk_text` when context present; `chunk_text` alone when null. Reranker receives full text plus context. | Already true in `enrichment.enrich_chunks` (line 324 — `enriched_text = f"{result.context_prefix}\n\n{chunk_text}"`). Unit 3 verifies. **Reranker side flagged in §10 as out-of-scope coordination.** | 3 (verification only) |
| REQ-4: `recontextualize_kb` is idempotent; re-running yields the same context for unchanged docs (caching). | Unit 4 implements the task with `content_hash`-keyed cache hit short-circuit. | 4 |
| REQ-5: Post-deploy on test-tenant: ≥10% improvement in `context_precision` on `chat` suite vs baseline. | Unit 5 runs the eval harness with `variant=contextual_v1` and gates on threshold. | 5 |
| REQ-6: < €0.05 per 8000-token document. | §3 cost analysis: ~€0.012 per 8000-token doc. Unit 5 verifies via LiteLLM token-count audit. | §3 + Unit 5 |

## 10. Coordination note for retrieval-api

The reranker in `klai-retrieval-api/retrieval_api/services/reranker.py` (and the rerank-input assembly in `services/search.py` / `services/synthesis.py`) currently operates on `chunk.text` (the original chunk text only). After this SPEC ships, the embedding model and sparse model both see `enriched_text = "{context_prefix}\n\n{original_text}"`, but the cross-encoder reranker still receives `chunk.text`.

Anthropic's full pattern recommends the reranker also see the prepended context, so retrieved candidates are re-scored against the same input the embedding saw. The discrepancy is observable as: a chunk that ranks well on dense + sparse (because its summary-derived `context_prefix` matches the query) may be down-ranked by the cross-encoder which only sees the bare chunk text.

**Action.** Open a follow-up SPEC (proposed: `SPEC-RAG-RERANK-CONTEXT-001`) once SPEC-RAG-CONTEXTUAL-001 ships and the eval-variant uplift has been measured. The follow-up:
- Switches the reranker input from `chunk.text` to `chunk.text_enriched` (already in payload — no schema change).
- Verifies cross-encoder input-length budget (Infinity reranker on gpu-01; Anthropic uses the same pattern with their voyage-reranker, so length should be fine).
- Re-runs eval harness with `variant=contextual_v2_rerank` to measure incremental delta.

Not blocking for this PR; flagged here so the coordination is on the record.

## 11. Open items needing Mark's input before /run

1. **Dutch summary prompt language.** Should the document_summary prompt be Dutch (matching the existing `enrichment.py` Dutch prompt), English (matching klai-fast's stronger English distribution), or auto-detected per artifact? Recommendation in plan: Dutch when the artifact's primary language is Dutch (most Voys content is), English fallback otherwise. Trade-off: Dutch summaries embed slightly weaker but stay on-language with the chunk text; English summaries embed stronger but introduce a language mismatch in the prepended prefix. Quick eval on a 10-doc sample during Unit 5 settles it; flag if Mark wants the language locked in advance.
2. **Reranker coordination timing.** Is the §10 follow-up SPEC something to write now (so the run-phase commits include a stub spec.md for the next SPEC), or after eval results from this PR? Default plan: write the stub stub now; deeper plan after eval.
3. **`recontextualize_kb` audience.** The runbook is operator-facing. Is this a Mark-only command, or do we want a portal-side admin button (deferred SPEC) so future tenant admins can self-trigger? Default plan: Mark-only via CLI; portal button is a Tier 3 follow-up.
