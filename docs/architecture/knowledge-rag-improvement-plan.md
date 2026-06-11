# Klai Knowledge / RAG / Retrieval Improvement Plan

Generated: 2026-06-11 (v2 — merged)

Provenance: this is a merge of two independent analyses produced on 2026-06-11:

- **v1 (Codex session)** — preserved at `.context/klai-knowledge-rag-improvement-plan-v1-codex.md`. Direct source inspection + Serena; CodeIndex unavailable (corrupted WAL).
- **v2 (this session, Claude)** — all architecture docs read in full, code verification per gap cluster against the 2026-06-11 tree, plus targeted online research with WebFetch-verified sources.

Evidence markers used throughout: **[doc]** = from repo docs, **[code ✓]** = verified against source on 2026-06-11 with file:line, **[online]** = web research with verified URL, **[not verified]** explicit.

Implementation update (2026-06-11, Codex): Phase 0.3/0.4 first slice landed in code. Scored eval runs now require `reference_answer`, RAGAS receives the full reference answer (no implicit `expected_topics` fallback), shipped suites have `reference_answer`, `expected_chunks` canaries hard-fail before fuzzy scoring, and Grafana alert `rag_eval_canary_dropped` + runbook were added. Phase 0.2 temporal hygiene also landed: same-path re-ingest now records the PG `superseded_by` chain after the replacement artifact exists, `valid_from`/`valid_until` get Qdrant integer payload indexes, and tests cover supersession linking plus in-memory Qdrant delete-then-upsert behavior. Remaining before relying on the new eval numbers: live `manual-canary-debug` verification of the dormant canary markers, then recapture `baseline-v5`.

Implementation update (2026-06-11, Codex): Phase 0.5 light consistency watch landed. `reconcile_pg_qdrant` runs as a nightly read-only Procrastinate task, compares active synced PG artifacts with distinct Qdrant artifact payloads, logs `pg_qdrant_reconcile`, and Grafana alert `pg_qdrant_reconcile_failed` fires on discrepancies. It deliberately does not repair drift; the full outbox remains H2.

Review status (2026-06-11, evening): commits `a47a5e78f` (Phase 0.2 temporal
hygiene) and `41f2a449f` (Phase 0.5 PG↔Qdrant consistency watch) received their
**independent adversarial review** (two skeptical evaluator agents, one per
commit). Both verdicts: PASS-WITH-ISSUES, no blocking findings. All HIGH/MEDIUM
findings were fixed in the same session with regression tests:

- *Temporal (a47a5e78f)*: timestamp-equality supersession linking replaced with
  id-based linking (`soft_delete_artifact` RETURNING id → `set_superseded_by`),
  and soft-delete + create + supersede-link now run in one PG transaction.
- *Reconcile (41f2a449f)*: a crashed job now still emits `pg_qdrant_reconcile`
  with `status=error` (previously a crash produced **no** event and the alert
  silently never fired — refuted claim); alert expr extended to
  `status:failed OR status:error`; 15-min race-tolerance window via
  `created_at` cutoff + recent-keys exclusion; 15-min `asyncio.timeout`; task
  moved from the latency-sensitive `ingest-kb` IO lane to the `rag-eval`
  nightly batch lane.
- *Reconcile hardening follow-up*: `pg_qdrant_reconcile_missing` now acts as a
  dead-man alert when no reconcile event appears in 25h, and Grafana runbook
  links are absolute GitHub URLs.

Independent Codex review (2026-06-11, post-merge, commits `98b8de3d3..cac43b5c5`):
verdicts D1/D2/D3/D6 AGREE, D5 resolved by the `pg_qdrant_reconcile_missing`
dead-man alert, and two actionable items:

- **D7 MEDIUM — fixed**: the `scope=both + kb_slugs` personal bypass in
  `retrieval_api/services/search.py` matched on `user_id`, which also passed
  user-stamped chunks in NON-selected org KBs (selected-KB semantics widened;
  not a cross-user leak — the visibility filter still blocked other users'
  private chunks). Replaced by a canonical-`personal_kb_slug` match + regression
  test `test_kb_slugs_both_scope_does_not_pass_user_stamped_chunks_in_unselected_kbs`.
- **D4 — accepted architectural debt**: the reconcile job runs on the
  `rag-eval` lane, which is documented as LLM-bound while the job is IO-bound.
  Works tactically; a dedicated `CONSISTENCY`/batch-IO queue is the structural
  fix. Do this when the next nightly batch job lands, not as a standalone PR.

---

## 0. The backlog itself is stale — refresh it first

`docs/architecture/product-gaps-backlog.md` (2026-06-08) is already overtaken on **five** items **[code ✓]**. Any planning that starts from the backlog without this correction plans work that is already (half) done — the v1 plan partially fell into this trap (see chunk_type below).

| Gap | Backlog claim | Actual state 2026-06-11 |
|---|---|---|
| `GAP-TEMPORAL-01` | retrieval filters on never-written `invalid_at` | **Fixed on the serving path** (re-verified 2026-06-11): `search.py:71-112` has a dual-contract `must_not` over `invalid_at`/`valid_until`/`valid_at`/`valid_from`, with a passing integration test (`test_search.py:129-218`). The retro's open question is **answered**: both `qdrant_store.upsert_chunks` and `upsert_enriched_chunks` delete all points for `(org, kb, path)` before upserting — same-path re-ingest leaves no stale points (the random `uuid4` point IDs are harmless; delete-by-path-filter is the mechanism, not overwrite-by-ID). Temporal hygiene is now also covered in code: replacement ingests link the just-closed PG artifact row via `superseded_by`, and `valid_from`/`valid_until` get Qdrant integer payload indexes. Remaining risk: a failed Qdrant delete after PG commit is silent divergence (→ H1 / GAP-SYNC-01). |
| `GAP-RETR-01` | gate inert, reference file missing | `gate_reference.jsonl` **exists** (16-line generic stub) and the gate runs in **shadow mode** (`retrieval_gate_shadow=True`, `config.py:28`), logging `gate_would_bypass` per request; strict mode skips the gate by design (`gate_skipped_reason=strict_mode`, `retrieve.py:213-220`). Blocker is now corpus quality, not existence. |
| `GAP-TENANCY-01` | `is_tenant` missing | Code sets `is_tenant=True` for **new** collections (`qdrant_store.py:85-102`); the existing prod collection awaits a one-time online migration via `scripts/upgrade_org_id_tenant_index.py`. |
| `GAP-INGEST-02` | `chunk_type` classified but never read | **Resolved by removal** on 2026-06-08 (`enrichment.py:10-14`, rationale in `docs/research/chunk-type-retrieval-value.md`). Close the gap. ⚠️ The v1 plan's "chunk_type serving experiment" (its quick win #5 and theme 7) is based on this stale claim and has been **dropped** from the merged plan. |
| `GAP-EVID-01` | assertion-mode weight is constant 1.00 | `_assertion_weight` now returns profile weights (factual/procedural 1.00, hypothesis 0.90, unknown 0.97; `evidence_tier.py:130-145`) — still shadow-gated, **and the RAGAS A/B from SPEC-EVIDENCE-001-FOLLOWUP-001 was never built** (no `evidence_tier_full` / `evidence_tier_temporal_only` variant anywhere in the eval code; only `baseline` is used). The 30-day decision deadline has lapsed. |

All other gaps (`LOOP-*`, `PROV-*`, `SYNC-01`, `TAX-*`, `MCP-01`, `PRIV-01`, `EVAL-01/02`, `ROUTE-*`) were re-confirmed today exactly as described **[code ✓]**.

**Action (Phase 0.1):** refresh `product-gaps-backlog.md` + the inline "Intended vs. current" callouts in the architecture docs.

---

## 1. Executive Summary

### Top 5 we SHOULD do

1. **Fix evaluation first (GAP-EVAL-01 + 02) — code slice landed, live baseline pending.** The 2026-06-11 patch requires `reference_answer` for scored suite runs, removes the implicit topic-label fallback, and hard-fails `expected_chunks` canaries before fuzzy scoring. Every activation decision (evidence tier, gate, taxonomy, Tier-3) still gates on these numbers, so the next operational step is live canary verification + recapturing `baseline-v5`.
2. **Temporal correctness is now mostly closed; keep the consistency guard next.** Re-verification (2026-06-11) showed the serving path is safe: dual-contract filter + delete-then-upsert inside both Qdrant upsert functions. The follow-up slice now links superseded PG artifacts, adds temporal payload indexes, and tests same-path re-ingest cleanup. The remaining material risk is dual-store divergence if Qdrant delete/upsert fails after PG state changes (#4).
3. **Force the evidence-tier shadow-mode decision.** Shadow has been running since March, pays `deepcopy + apply()` CPU on every request, and the FOLLOWUP-001 deadline passed without the A/B ever being built **[code ✓]**. Activate / temporal-only / decommission / flags-off — but decide.
4. **PG↔Qdrant consistency watch (light GAP-SYNC-01) — code slice landed.** Not the outbox yet: the nightly read-only reconciliation count + alert now exists. Next step is to inspect the first production baseline before deciding whether H2 outbox work is justified.
5. **Run the Qdrant `is_tenant` migration** on the existing collection (script exists; Qdrant supports zero-downtime online reindex — [Qdrant FAQ](https://qdrant.tech/documentation/faq/qdrant-fundamentals/) **[online]**). This is the recall/scale guarantee the single-collection decision was built on.

### Top 5 quick wins (S, close to config)

1. **Generate the full gate corpus** (`scripts/generate_gate_reference.py`, 200 queries, 6 languages) + analyze 1–2 weeks of `gate_would_bypass` shadow telemetry before activation.
2. ~~**Eval canary hit/miss check**~~ — landed 2026-06-11: hard-fail before aggregate RAGAS scoring + `rag_eval_canary_dropped` alert. Live canary debug still needed before trusting the HIGH alert.
3. **Backlog/doc refresh** (section 0).
4. **MCP `search_knowledge` parameters** — `scope`, `kb_slugs`, `content_type`, `time_range`, `top_k` instead of hardcoded `scope:"both"` (`main.py:1103`).
5. **Gap priority scoring urgency×recency (GAP-LOOP-04)** — scoring only, no schema change needed for v1.

### Top 3 to DEFER

1. **GraphRAG community summaries / agentic query decomposition** — literature confirms GraphRAG often underperforms vanilla RAG on simple fact retrieval ([arXiv:2506.05690](https://arxiv.org/abs/2506.05690) **[online]**); roadmap correctly says: only after 4 weeks of production traces, picked by dominant failure mode.
2. **Assertion-mode activation (SPEC-EVIDENCE-002) + corroboration scoring** — own research: flat weights until calibration data exists; 4 multiplicative dimensions at ~85% accuracy ⇒ 48% chance of ≥1 misclassification per chunk **[doc]**. Evidence-tier base decision (G1) first.
3. **Full dual-store outbox + autonomous taxonomy self-maintenance** — both L/XL; the light variants (read-only reconciliation, re-cluster *proposals* with human gate) capture most value at a fraction of the risk. Cross-org federation (GAP-FED-01) stays explicitly out of scope.

---

## 2. Current Architecture Map (verified)

### Ingest path **[doc+code ✓]**

Sources: Gitea webhook (3-min debounce) / klai-connector adapters (github, notion, airtable, confluence, google_drive, ms_docs) / web crawl inside knowledge-ingest / portal uploads via docling-serve / scribe transcripts / MCP saves.

Main route `routes/ingest.py::ingest_document_route` → `ingest_document` (step order, from v1 analysis, consistent with v2 verification):
1. Identity assertion on `/ingest/v1/document`
2. Content normalization + SHA-256 hash dedup
3. Chunking (`chunker.chunk_markdown_with_parents`, parent-child) or adapter-prechunked
4. Dense embeddings (TEI BGE-M3) — document searchable immediately
5. Content label + taxonomy classification (if KB has nodes)
6. Soft-delete previous artifact for same path (PG; **Qdrant side unverified — see A1**)
7. PG artifact row (`pg_store.create_artifact`, incl. `derivations` for valid `derived_from`)
8. Parent chunks insert
9. Qdrant upsert (random uuid4 point IDs)
10. PG extra/status update
11. Enqueue enrichment (Procrastinate, 9 queues / 2 lanes) — document_summary + context_prefix + HyPE + sparse
12. Enqueue Graphiti episode (FalkorDB) + PageRank writeback

Key files: `routes/ingest.py`, `pg_store.py`, `qdrant_store.py`, `enrichment.py`/`enrichment_tasks.py`, `enrichment_policy.py`, `graph.py`, `chunker.py`, `contextual.py`.

### Retrieval / chat path **[doc+code ✓]**

LibreChat / Partner API / widget → LiteLLM `KlaiKnowledgeHook` (`deploy/litellm/klai_knowledge.py` + `klai_kb_*` modules): trivial check → feature/scope lookup (30s/300s two-level cache) → taxonomy trees+coverage (Redis) → combined rewrite+classify in one `QUERY_REWRITE_MODEL` call → `retrieval-api /retrieve` (`api/retrieve.py::retrieve`):

identity verify → coreference (skipped when caller pre-resolved) → dense+sparse embed → **gate (shadow)** → **router** (source labels; keyword/centroid layers, LLM layer dead) → Qdrant native RRF (`FusionQuery`, prefetch `max(candidates×4, 20)`; 2–5 legs + parallel Graphiti leg) → link expansion + authority boost → Infinity rerank (top 20) → quality boost (≥3 votes, ±10%) → source-aware select → parent expansion → **evidence-tier shadow scoring** → EvidencePack.

### Citation path **[doc]**

`EvidencePack` (`retrieval_api/models.py`) — `items` (citable evidence) + `sources` (max 3, deduped on normalized URL) → `_klai_kb_meta` → post-call guard `klai_kb_citation_render.py`. Strict = fail-closed deterministic refusal (6 prompt modes, `klai_kb_chat_mode.py`); sources never come from model text or raw chunk URLs. Web Search is orthogonal: never widens strict mode, never enters the KB EvidencePack.

### Eval / observability path **[code ✓]**

Nightly `@app.periodic` RAGAS (02:00 UTC per suite, `ragas_runner.py`) → `knowledge.rag_eval_results` → Grafana `rag-quality` + `rag_eval_faithfulness_low` alert (<0.80) + `rag_eval_canary_dropped` alert. 4 metrics: context_precision/recall (klai-fast), faithfulness (klai-medium, max_tokens 8192), answer_relevancy (klai-fast + bge-m3). Scored suite runs require `reference_answer`; `expected_chunks` canaries are checked before judge/RAGAS scoring. `retrieval_decision_record` carries per-step latencies (coreference_ms, embedding_ms, gate_ms, search_ms, graph_search_ms, link_expand_ms) + gate/router/shadow metadata. VictoriaLogs by `request_id`.

Known weakness now: the shipped `reference_answer` fields are first-pass human-authored answers and need source review against the live Voys KB before the numbers are used as a product decision gate. The dormant `expected_chunks` markers also need a live `manual-canary-debug` run.

### Self-improvement / gaps path **[code ✓]**

Hook `classify_gap()` (hard/soft, thresholds 0.4/0.35, env-tunable; `klai_retrieval_telemetry.py:84-115`) → fire-and-forget `/internal/v1/gap-events` (`internal.py:1191-1305`; telemetry levels off/shadow→`[REDACTED:shadow]`/full) → `portal_retrieval_gaps` (exact-string grouping, only `resolved_at`; `retrieval_gaps.py:22-53`) → `/app/gaps` inbox (`app_gaps.py`, frequency-ordered) → `gap_rescorer.py` (max 50 queries, re-retrieve, auto-resolve via timestamp).

Missing: semantic registry, LLM judge, lifecycle, article backlinks, transcript/unresolved-conversation arms, AI draft.

### MCP / agent path **[code ✓]**

`klai-knowledge-mcp/main.py` — write tools `save_personal_knowledge` (:721), `save_org_knowledge` (:790), `save_to_docs` (:862) with `assertion_mode` + `derived_from`; read tool `search_knowledge(query, kb_slug)` with hardcoded `"scope": "both"` (:1103). Identity via portal-api `/internal/identity/verify` (`_identify_request`, :499-525). Failure mode raises `ToolError` — correct, keep.

### Transcript path **[code ✓]**

scribe-api `POST /v1/transcriptions/{id}/ingest` (`transcribe.py::ingest_transcription_to_kb`) → `knowledge_adapter.ingest_to_kb()` → `/ingest/v1/document` (`meeting_transcript`/`1on1_transcript`, opt-in). STT: `whisper_http` provider → gpu-01 tunnel `172.18.0.1:8000` (Vexa transcription-service behind the tunnel; scribe config naming is drift, not a functional issue). **No** transcript→gap arm. *(Verified 2026-06-11: the ingested content is the **full transcript text** — `knowledge_adapter.ingest_scribe_transcript` sends `full_text`; the summarizer is a separate feature. The old "scribe-api summarizes before ingest" doc claim was wrong and has been corrected in `knowledge-ingest-flow.md` §1.3.)*

---

## 3. Improvement Plan by Theme

> Per improvement: Problem · Why now · Evidence · Target behavior · Code paths · Data/config · Tests · Rollout/metrics · Failure modes · Dependencies · Effort · Confidence/unknowns · Research. Explicit NOT-do items per theme.

### Theme A — Temporal correctness

#### A1. Temporal hygiene — test, `superseded_by`, index (remainder of GAP-TEMPORAL-01)

> **Re-scoped 2026-06-11.** The Phase-0 verification this item originally called for is
> done: both `qdrant_store.upsert_chunks` and `upsert_enriched_chunks` perform a
> delete-by-`(org, kb, path)` *inside* the store function before upserting **[code ✓]**,
> so same-path re-ingest physically removes superseded chunks and the random `uuid4`
> point IDs are harmless. The serving bug is closed; what's left is hygiene.

- **Status (2026-06-11): code slice landed.** `soft_delete_artifact()` returns the
  close timestamp; `ingest_document()` creates the replacement artifact and then links
  rows closed at that timestamp via `set_superseded_by_for_path()`. `valid_from` and
  `valid_until` now get Qdrant integer payload indexes. Tests cover PG supersession
  linking, ingest propagation, in-memory Qdrant re-ingest cleanup, and the existing
  retrieval temporal filter contract.
- **Problem (residual):** the only stale-serving path left is a Qdrant delete/upsert
  failure after PG state changes — that is dual-store divergence, owned by H1/H2
  (`GAP-SYNC-01`).
- **Why now:** done while verified knowledge was fresh; the regression tests keep the
  now-correct behavior correct.
- **Target:** keep dual-contract (legacy + current) filter support until payloads are
  migrated (v1 point — adopted). Next reliability work is the PG↔Qdrant consistency
  watch, not more temporal filter surgery.
- **Code:** `pg_store.py:231-248` (`superseded_by`), `qdrant_store.py` (index creation),
  new test in ingest or retrieval-api test suite. Retrieval side is done.
- **Data/config:** no migration; existing collections create the new Qdrant indexes on
  `ensure_collection()`. Current payloads store these fields as epoch integers, so the
  index type is `integer`.
- **Tests:** `test_pg_store.py::test_set_superseded_by_for_path_links_only_just_closed_records`,
  `test_ingest_content_hash_dedup.py::test_proceeds_when_content_changed`,
  `test_personal_kb_e2e.py::test_upsert_chunks_reingest_removes_old_points_for_same_path`,
  `test_qdrant_link_counts.py::test_ensure_collection_creates_temporal_indexes_when_missing`,
  and the existing `test_search.py::test_qdrant_temporal_filter_*` contract.
- **Rollout/metrics:** no flag; one-time audit query (Qdrant points per artifact whose PG row is superseded) as baseline and regression metric. Optional: `temporal_filter_contract_version` in the decision record (v1).
- **Failure modes:** batched `set_payload` on large artifacts; clock skew; mixed ISO/epoch fields; legacy payloads missing both contracts (v1).
- **Effort: S.** Confidence: high — filter, delete-then-upsert, and tests all verified against source 2026-06-11.

### Theme B — Retrieval gate / routing / latency

#### B1. Gate: corpus + shadow evaluation + controlled activation (remainder of GAP-RETR-01)

- **Problem:** gate runs shadow with a 16-line generic stub corpus; bypass never happens, so every trivial query pays embed + 5-leg + rerank (GPU + 300–500ms).
- **Target:** (1) generate the full corpus (script: 200 queries, 100 no-retrieval + 100 retrieval-needed, 6 languages NL/EN/DE/FR/PT/ES), versioned (corpus version field — v1); (2) 1–2 weeks of `gate_would_bypass` telemetry vs real queries, **per-tenant and per-language false-bypass metrics** (v1 — adopted; multilingual mismatch is a real failure mode); (3) only then `retrieval_gate_shadow=false`. Strict mode stays gate-free (already enforced).
- **Research [online]:** training-free gating evidence: TARG (logit-margin on draft prefix) reaches 70–90% fewer retrievals at matched EM/F1 ([arXiv:2511.09803](https://arxiv.org/abs/2511.09803)); production frameworks (LangGraph/LlamaIndex) default to a cheap structured-output LLM router. For Klai, the embedding-margin gate is fine as v1; a later upgrade can piggyback a `needs_retrieval` field on the existing rewrite call (zero extra roundtrip).
- **Code:** `services/gate.py`, `scripts/generate_gate_reference.py`, `data/gate_reference.jsonl`, deploy step that ships the corpus. Tests: existing `test_gate.py` (8) + corpus snapshot tests, NL/EN trivial-bypass expectations, strict-never-bypasses (v1 list — adopted).
- **Failure modes:** false bypass in open mode = silently no KB context → manual review of a 50-query would-bypass sample before activation; corpus overfit to generic assistant tasks (v1).
- **Effort: S (corpus+telemetry), M (activation incl. analysis).** Confidence: high.

#### B2. Model routing: LiteLLM native complexity router pilot (GAP-ROUTE-01/02)

- **Problem:** custom 3-signal router mis-routes (short complex question → fast; long trivial paste → large); `klai-medium` is only a quota fallback, never a routing target.
- **Research [online]:** LiteLLM now ships a **native complexity router**: rule-based, 7 dimensions (code 0.30, reasoning 0.25, technical terms 0.25, tokens 0.10, …), tier boundaries 0.15/0.35/0.60, <1ms, no external calls ([docs.litellm.ai/docs/proxy/auto_routing](https://docs.litellm.ai/docs/proxy/auto_routing)). Open bugs around content-array (multimodal) messages — test on Klai's message shapes first **[not individually verified]**. RouteLLM is the heavier trained upgrade (GPT-4-era benchmarks).
- **Target:** shadow-run the native router alongside `custom_router.py` (log both decisions), compare on real traffic, then decide; include `klai-medium` as mid-tier target. At cutover remove the custom router (clean over parallel old+new).
- **Ops note:** litellm deploys require `compose-up.sh --force-recreate` (bind-mounted Python module cache pitfall).
- **Effort: M.** Confidence: medium — depends on LiteLLM version/bug status.

#### B3. ROUTE-03 dead code + latency map

- Retrieval router Layer-3 LLM fallback is dead (`llm_fn` never passed, `router_llm_fallback=False` default; `router.py:290-306`, `config.py:94`) — remove or deliberately activate; dead code is the worst option. **Effort: S.**
- Build one Grafana panel (p50/p95 per pipeline step) from the existing `retrieval_decision_record` latencies before deciding any optimization (incl. link-expansion F3 phase 2, already waiting on 7 days of telemetry). **Effort: S.**

### Theme C — Qdrant multitenancy & scale

#### C1. `is_tenant` migration on the existing collection (GAP-TENANCY-01)

- **Problem:** correct isolation via filter, but no tenant co-location → sequential-read benefit and recall/latency guarantee absent for the existing prod collection.
- **Research [online]:** `is_tenant: true` is a keyword-index option (v1.11+) that physically co-locates tenant data "at the next optimization" ([multitenancy docs](https://qdrant.tech/documentation/manage-data/multitenancy/), [1.11 release](https://qdrant.tech/blog/qdrant-1.11.x/)). Migration path: drop + recreate the index with `is_tenant=True`, then trigger HNSW rebuild via a minimal `ef_construct` change — "Queries continue to be served by the old index until the new index is complete, so there is no downtime" ([FAQ](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)). No official quantitative benchmarks exist — don't overclaim. Qdrant's own guidance: "Do not skip is_tenant=true on the tenant index".
- **Target (merged with v1's verifier idea):** (1) **startup/admin health check that reports tenant-index status** (never auto-drop/rebuild on startup); (2) run `scripts/upgrade_org_id_tenant_index.py` on a snapshot first, then prod; (3) before/after Qdrant schema snapshot + p95 search latency per tenant.
- **NOT:** `m=0/payload_m=16` per-tenant HNSW subgraphs (makes cross-org queries — analytics, dedup, eval — slow; full index reconstruction). Tiered sharding (Qdrant 1.16, promotion threshold ≈20k points/tenant) only when one tenant dominates.
- **Failure modes:** recreate window = full-scan fallback (correctness intact, latency up); optimizer CPU during rewrite; script run against wrong environment (v1).
- **Effort: S (run) + verifier.** Pre-check: confirm deployed Qdrant server version ≥1.11 (≥1.16 for tiered) on core-01 **[not verified]**; script completeness **[not verified]**.

#### C2. Filter-field index audit

Every payload key used in filters must have an index — unindexed filter fields break Qdrant's cardinality estimator ("extremely slow search times or low accuracy results", [filtering article](https://qdrant.tech/articles/vector-search-filtering/) **[online]**). `valid_from`/`valid_until` are now indexed via A1; audit the other filter fields against actual query/delete filters. **Effort: S.**

### Theme D — Evaluation quality

#### D1. Real reference answers (GAP-EVAL-01)

- **Status (2026-06-11): code slice landed.** `reference_answer` is present in shipped suites, required for scored runs, and passed to RAGAS; there is no implicit fallback to `expected_topics`. Old baselines are not comparable.
- **Original problem:** `reference = ', '.join(expected_topics)` — RAGAS decomposes the reference into claims; a keyword label turned context_precision/recall partly into keyword-overlap measurement. The headline numbers (+61%/+154%) were directional, not evidential.
- **Research [online]:** RAGAS requires a full reference answer ([context_recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)); `TestsetGenerator` (0.4.x) synthesizes golden sets with reference contexts ([testset generation](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/)); sample size ~246 for 5% margin at 95% confidence; Wilcoxon for ordinal metrics ([arXiv:2506.13023](https://arxiv.org/html/2506.13023v1)).
- **Still open:** source-review the first-pass `reference_answer` fields; add `must_not_chunks`, `query_type`, `language`, `kb_mode`; grow toward ~150–250 cases; use Wilcoxon instead of bare means.
- **Important:** re-capture `baseline-v5` after live canary verification — old and new numbers are not comparable.
- **Effort remaining: S–M.** Confidence: high on the code contract; medium on answer quality until source review.

#### D2. Canary hit/miss check (GAP-EVAL-02)

**Status (2026-06-11): code slice landed.** `expected_chunks` are checked before judge/RAGAS scoring. Missing canaries write NULL metrics with `meta.canary.passed=false`, skip fuzzy scoring, and trigger Grafana alert `rag_eval_canary_dropped`. The matcher avoids generic body-text false positives by requiring short markers to match strong fields; body-text matches are reserved for longer phrase markers. **Still open:** live `manual-canary-debug` verification because these dormant markers were never production-tested.

#### D3. Hook-path eval mode

The harness bypasses the LiteLLM hook, so rewrite/taxonomy/gate are unmeasurable **[doc]**. Add a second eval mode through LiteLLM `/v1/chat/completions` (eval-org team key) so the full chain is measured; weekly instead of nightly (cost). Store `retrieval path` (direct vs hook) with each result row (v1). This also unlocks gate and evidence-tier A/Bs on the real route. **Effort: M.**

### Theme E — Self-improving gap loop (flagship)

Order: signal quality first (E1–E3), then workflow (E4), then scale (E5).

#### E1. Semantic gap dedup (GAP-LOOP-02, first step)

- **Problem:** exact-string grouping (`app_gaps.py:88`) — 20 paraphrases = 20 low-frequency rows; prioritization structurally undercounts demand.
- **Target v1 (deliberately smaller than the doc):** no separate Qdrant gap collection. Embed `query_text` at insert (BGE-M3 available), match against the org's open gaps with a cosine threshold (~0.85–0.9), increment `frequency`/`last_seen` on match. HDBSCAN clustering is v2 for the inbox view; noise points = genuinely new ([NeMo SemDedup pattern](https://docs.nvidia.com/nemo-framework/user-guide/24.09/datacuration/semdedup.html) **[online]**; assembled practice, no single authoritative production write-up).
- **Design choice needed:** pgvector on portal DB vs small Qdrant collection vs **in-process cosine over capped open-gap set** (likely sufficient and simplest at current volumes).
- **Data model (v1 plan's richer shape — adopt at E4 scale-up):** `portal_gap_clusters` (canonical query, embedding, representative examples, variants, status, taxonomy nodes, linked artifact/proposal, priority score) + `portal_gap_cluster_events` + `portal_gap_reviews`, with RLS policies and a retention/telemetry policy for raw query text.
- **Failure modes:** over-merge (different questions fused → frequency inflation) vs under-merge; start conservative, log merge decisions, label ~100 gap pairs first to pick the threshold.
- **Effort: M.**

#### E2. LLM-as-judge coverage classification (GAP-LOOP-01)

- **Target:** async (existing `_classify_gap` background task in portal-api, or a Procrastinate task): judge over query + top-3 chunks → verdict + `missing_aspects[]` + candidate `article_id`. **Label set (merged):** `covered | partial | new` core (architecture §8.2) extended with v1's editorial labels `stale/outdated`, `contradictory`, `ambiguous_query`, `not_a_kb_gap` — the extended set maps to concrete editor actions.
- **Method:** small model (klai-fast), strict rubric, step-by-step reasoning, calibrate against ~100 hand labels first (binary/low-cardinality preference per [Evidently's judge guide](https://www.evidentlyai.com/llm-guide/llm-as-a-judge) **[online]**; the oft-quoted ~83% agreement figure could **not** be verified).
- **Data:** new columns on gap rows/clusters (`verdict`, `missing_aspects` JSONB, `related_artifact_id`).
- **Failure modes:** judge cost under gap storms → rate limit + only judge deduped representatives (dependency: E1); hallucinated article_id → constrain choice to the top-3 chunks' artifacts.
- **Effort: M–L.**

#### E3. Transcript → gap arm + unresolved conversations (GAP-LOOP-05)

- **Problem:** only low-confidence chat retrieval feeds the loop; the richest source — real conversations where the answer was missing — does not. The doc's third arm (conversations marked unresolved, e.g. partner/widget flows) is also absent (v1 kept this — adopted).
- **Target v1:** on transcript ingest (existing opt-in path), an extraction step: LLM extracts **unanswered questions, decisions, action items implying missing documentation** (v1's broader extraction set — adopted) → per item a retrieve → classify/judge → gap event with `source='transcript'`. Procrastinate LLM lane, batch, no latency pressure. Candidates only — never automatic KB articles.
- **Privacy edge (merged):** transcripts are PII-rich; reuse the existing telemetry-level redaction (`internal.py:1226`); add **PII detection/pseudonymization** where needed ([Microsoft Presidio](https://microsoft.github.io/presidio/) **[online]** is the credible building block — v1); treat transcript text as **untrusted input** (prompt-injection inside transcripts is a real failure mode — v1); per-tenant opt-in + user-visible disclosure. **Needs product/legal decision before activation.**
- **Tests (v1 — adopted):** extractor treats transcript as untrusted; PII redaction; off/shadow/full telemetry; transcript delete/offboarding cascades.
- **Effort: L.** Dependencies: E1+E2 (don't flood the inbox), privacy decision.

#### E4. Lifecycle + priority (GAP-LOOP-03/04)

`status: open | in_progress | drafted | resolved | dismissed | reopened` (v1's richer set — adopted; superset of the doc's three) + `resolving_article_id` backlink + re-open after ≥3 reappearances. Priority `frequency × urgency_weight × recency_factor` (urgency heuristic: error-code regex 2.0, escalation words 1.5 — §8.4). Portal migration: mind the RLS migration pitfalls (post-deploy SQL for owner-required DDL; no UPDATE on Cat-A tables in alembic). **Effort: M.**

#### E5. AI-draft affordance (GAP-LOOP-06)

"Generate draft" button in the inbox: judge output (missing_aspects) + top chunks + KB style → BlockNote draft via existing docs path (`docs_client.py`). Human publication stays the gate (platform.md 90/10 principle). Metrics: editor accept/dismiss rate, reopened rate (v1). **Effort: L.** Dependency: E2.

### Theme F — Taxonomy

#### F1. Fractional coverage (GAP-TAX-02)

`get_kb_taxonomy_coverage` returns 1.0 iff KB has ≥1 node **[code ✓]**. Replace with fraction of active chunks carrying taxonomy labels (one aggregation over Qdrant facets or PG), plus **classification staleness/age tracking** (v1 — adopted). Makes the hook threshold (`KLAI_TAXONOMY_COVERAGE_THRESHOLD=0.30`) meaningful. Optional materialized stats table. **Effort: S–M.**

#### F2. Re-cluster proposals, not self-maintenance (GAP-TAX-01, deliberately scaled down)

Monthly Procrastinate task (queue `taxonomy-backfill` exists) running `generate_bootstrap_proposals_v2` (UMAP n_components=10 + HDBSCAN leaf, adaptive min_cluster_size **[code ✓]**) writing **proposals** (new nodes / merges / outlier rate) to an admin review list. Own research + industry (Zendesk/Intercom/Tettra): human gate mandatory, no autonomous taxonomy evolution **[doc]**. Query-time narrowing requires coverage + classifier confidence + empty-result fallback (v1). **Effort: M.** Dependency: F1.

#### F3. Taxonomy pilot tenant (GAP-TAX-03)

The chat path already sends `taxonomy_node_ids` when coverage exists (SPEC-RAG-TAXONOMY-001); it no-ops on Voys due to 0 curated nodes. The real fix is content: one tenant with a curated taxonomy as pilot + F1 measurement + recall-impact eval (taxonomy on/off variant). **Product action, no code effort.**

### Theme G — Evidence scoring / assertion mode

#### G1. Force the shadow-mode decision (SPEC-EVIDENCE-001-FOLLOWUP-001, lapsed)

- **Problem:** shadow running >2 months, `deepcopy+apply()` CPU per request, decision A/B never built **[code ✓]**.
- **Target:** (1) D1/D2 first; (2) implement the `evidence_tier_full` / `evidence_tier_temporal_only` variants (retrieval-api must accept a variant parameter or per-run env); (3) 7 nights, Wilcoxon, decide per the four predefined outcomes (activate staged 5/50/100% / temporal-only / decommission / flags-off). **Activation criteria (merged, v1):** unknown-fraction acceptable, per-content-type lift positive, canaries don't regress, ≥+0.02 on precision AND faithfulness at p<0.05. If nobody runs the A/B: **flags-off as default** — the honest minimal outcome that stops the CPU cost.
- **Effort: M.** Dependency: D1/D2.

#### G2. Assertion-mode activation (GAP-EVID-01/02) — DEFER

Plumbing is ready (profile weights exist, shadow-gated). Activation is SPEC-EVIDENCE-002 with its own research preconditions (3-group taxonomy, max spread 0.20, never user-facing labels, label-quality audit first — frontmatter-authored assertion labels are inconsistent, v1). **Effort at activation: S; the calibration is the work.**

#### chunk_type — closed

Removed 2026-06-08 **[code ✓]**. The v1 plan's chunk_type experiment is dropped. If a future SPEC wants chunk-type-aware retrieval, it re-opens `docs/research/chunk-type-retrieval-value.md` first.

### Theme H — Provenance & PG↔Qdrant consistency

#### H1. Read-only reconciliation job (light GAP-SYNC-01) — Phase 0/1

- **Problem:** write path is synchronous PG-then-Qdrant without compensation (`routes/ingest.py:554-680` **[code ✓]**); Qdrant failure after PG commit = silent divergence; happened before.
- **Status (2026-06-11): code slice landed.** Nightly task compares active synced PG artifacts against distinct Qdrant `(org_id, kb_slug, path, artifact_id)` payloads via scroll. It logs `pg_qdrant_reconcile` with missing/orphan counts and samples; Grafana alert `pg_qdrant_reconcile_failed` fires on `status=failed` or `status=error`, and `pg_qdrant_reconcile_missing` fires when no event appears in 25h. **Shadow only: report, never auto-delete.**
- **Target v1:** add operational baseline after the first production run and decide whether drift warrants H2 outbox work. Metadata-mismatch checks beyond artifact identity remain part of the broader audit.
- **Effort: M.**

#### H2. Outbox v2 — only if H1 proves drift

Activate `knowledge.embedding_queue` (schema exists, 0 INSERTs **[code ✓]**): INSERT in the same PG transaction → worker consume → idempotent Qdrant upsert/delete → mark processed. **Schema extensions (v1 — adopted):** processed/error/retry fields, dead-letter status, reconciliation report table. Tests: PG commit + Qdrant failure leaves queued retry; worker idempotency; delete queue removes all points; metrics: queue depth/age/retry/dead-letters, divergence by tenant (v1). Prereq: clear point-ID/idempotency strategy (today's random uuid4 IDs make idempotent re-upsert awkward — ties into A1). **Effort: L.**

#### H3. Provenance population (GAP-PROV-01/02)

`derivations` is populated for Docs artifacts with valid `derived_from` **[code ✓]**;
same-path re-ingest now sets `superseded_by` after the replacement artifact exists
**[code ✓]**; `entities`/`artifact_entities` still have 0 INSERTs. Plan: (1) fill the
entity registry from Graphiti output (entities already extracted; only the PG write is
missing) — **only when a consumer exists** (MCP `provenance_chain`/`related_concepts`,
Theme I); (2) confidence-from-source-count: defer (corroboration research lists 3
unmet prerequisites **[doc]**). Cross-org `derived_from` rejection test exists
conceptually (v1) — keep. **Effort: M–L / deferred.**

### Theme I — MCP read tools (GAP-MCP-01)

- **v1 (quick win, S–M): first slice landed 2026-06-11.** `search_knowledge` now
  accepts `scope: org|personal|both` (default both) and `kb_slugs` (clamped to 20;
  stripped under `scope=personal` so the documented "ignored for personal notes"
  holds), passthrough to `/retrieve` which already supported both. `top_k` already
  existed. Guards: `scope=personal` hard-fails without a verified user;
  adversarial review PASS + klai-tenant-review clean (cross-user personal-KB
  access via crafted `kb_slugs` refuted with code evidence — the `user_id`
  ownership branch in `_scope_filter` blocks it). **Still open for v1:**
  `content_type` and `time_range` — both need `RetrieveRequest` + filter-leg
  additions in retrieval-api first, deliberately deferred out of the passthrough
  slice.
- **v2 (M):** `recent(scope, kb_slug, content_type, since)` — PG query on `knowledge.artifacts`; `related_concepts(query|artifact_id, depth)` — Graphiti traversal (graph_search service exists).
- **v3 (L, dependency H3):** `provenance_chain(artifact_id)`, `belief_evolution(entity_or_query, time_range)`.
- **Security:** identity-verify path unchanged (every tool through `_identify_request`); `scope:"personal"` without verified user must **hard-fail**, never silently degrade to org; run klai-tenant-review on the PR. Keep `ToolError` fail-loud (v1).
- **Telemetry (v1 — adopted):** add tool name + scope to telemetry; MCP caller client ID already flows.
- **Failure modes:** LLM clients overusing broad search; tool descriptions creating privacy ambiguity; provenance tool exposing data before the model is ready (v1).

### Theme J — Personal knowledge privacy (GAP-PRIV-01)

- **Problem:** §10.2 verbatim: personal content is "**Not processed by the contextual retrieval or HyPE enrichment pipeline (BGE-M3 direct embedding only — avoids LLM processing of personal content until the legal basis is formally established)**" — but `enrichment_policy.py:18-40` has no personal branch and neither does the Graphiti enqueue (`routes/ingest.py:749-761`) **[code ✓]**. Personal notes go through the full LLM pipeline (Mistral API) today.
- **Why now:** a documented compliance boundary the practice doesn't honor. EDPB Opinion 28/2024 requires per-purpose necessity/balancing ([EDPB](https://www.edpb.europa.eu/news/news/2024/edpb-opinion-ai-models-gdpr-principles-support-responsible-ai_en) **[online]**); no DPA text blesses an "embedding-only carve-out" as a named pattern — it is a data-minimization measure (Art. 5(1)(c), [gdpr-info](https://gdpr-info.eu/art-5-gdpr/)) you justify in your own DPIA **[online]**.
- **Target:** first a **policy decision** (keep the carve-out, or amend the doc with a documented legal basis). If kept: skip-reason `personal_kb_enrichment_disabled` in `enrichment_policy.enrichment_skip_reason()` (personal identified via `kb_slug.startswith("personal-")`, `routes/ingest.py:521-536`) + the same gate on the Graphiti enqueue. Scope decisions per sub-pipeline (v1's split — adopted): dense ✓, sparse ✓ if confirmed self-hosted-only (gpu-01 sidecar — it is), contextual/HyPE ✗, Graphiti ✗, gap analytics: follow telemetry policy.
- **Rollout (v1 — adopted):** **shadow logging first** — count personal artifacts that *would* be skipped — then enable the gate behind a flag. Optional artifact field `privacy_processing_class` + processing-basis audit field.
- **Open issue:** existing personal content is already enriched — decide: re-ingest without enrichment (cleanup) or tolerate with a cutoff date. Retrieval keeps working (dense-only chunks are an existing covered path).
- **Tests:** personal ingest enqueues no enrichment and no Graphiti; org unchanged; personal retrieval still works; telemetry redaction per policy (v1).
- **Effort: S (code gate), L (policy/backfill).** Blocker: policy decision, not engineering.

### Theme K — Scribe / transcript integration

1. **Naming/doc drift (S, docs side DONE 2026-06-11):** `knowledge-ingest-flow.md` §1.3 now states the verified reality (full transcript ingested, not a summary; STT endpoint = Vexa transcription-service on gpu-01 behind the legacy `whisper_server_url` config name). Remaining optional code-side cleanup: rename the `whisper_*` config fields in scribe to match the actual service — low value, only do it alongside other scribe work.
2. **Transcript→gap arm:** see E3 (the §13.9 open question; POST-to-ingest already exists; the extraction arm is the new part — batch via Procrastinate, no streaming needed for v1).
3. **Transcript enrichment quality:** transcripts already get rolling_window + always-HyPE **[doc]**; no change until eval data (D1) says otherwise.

---

## 4. Prioritized Roadmap

### Phase 0 — bugs & measurement correctness (gates everything else)

| # | Item | Effort | Theme |
|---|---|---|---|
| 0.1 | Refresh backlog/docs (5 shifted gaps incl. chunk_type closure) | S | §0 |
| 0.2 | ~~Temporal hygiene~~ landed 2026-06-11: delete-then-upsert confirmed, PG `superseded_by` chain linked on replacement ingest, temporal Qdrant indexes added, regression tests added | S | A1 |
| 0.3 | Eval: ~~reference answers + suite schema~~ landed; source review + **new baseline-v5** pending | S–M | D1 |
| 0.4 | Eval: ~~canary hard-fail + alert~~ landed; live canary debug pending | S | D2 |
| 0.5 | ~~PG↔Qdrant reconciliation count~~ landed 2026-06-11: nightly read-only shadow job + `pg_qdrant_reconcile_failed` alert | M | H1 |
| 0.6 | Evidence-tier: build + run the A/B → decide (or flags-off) | M | G1 |
| 0.7 | GAP-PRIV-01 policy decision on the agenda (decision, not code) | — | J |
| 0.8 | ~~Qdrant tenant-index status verifier~~ landed 2026-06-11: `ensure_collection` logs `qdrant_org_id_tenant_index_status` (warning + remediation hint when the prod collection still has a plain keyword index); test `test_qdrant_org_id_index_reports_tenant_status` | S | C1 |

### Phase 1 — low-risk quick wins

`is_tenant` migration (C1, snapshot first) · filter-index audit + datetime index (C2) · gate corpus + shadow analysis → activation (B1) · MCP search params v1 (I) · gap priority urgency×recency (E4-light) · ROUTE-03 dead code removal (B3) · latency dashboard from decision records (B3) · privacy carve-out implementation once 0.7 is decided, shadow-first (J) · taxonomy fractional coverage (F1).

### Phase 2 — product differentiators

Semantic gap dedup (E1) → LLM judge (E2) → lifecycle/backlinks/inbox upgrade (E4) · transcript→gap arm behind opt-in + PII pipeline (E3) · unresolved-conversation arm (E3) · MCP `recent` + `related_concepts` (I-v2) · taxonomy re-cluster proposals with human gate (F2) + pilot tenant (F3) · hook-path eval mode (D3) · LiteLLM complexity-router shadow pilot (B2).

### Phase 3 — deeper architecture / big bets (only on data)

AI-draft in editorial inbox (E5) · outbox/write-path hardening if H1 proves drift (H2) · entity registry + `provenance_chain`/`belief_evolution` MCP tools (H3 + I-v3) · assertion-mode activation SPEC-EVIDENCE-002 (G2) · Tier-3 retrieval: HyDE vs GraphRAG community summaries vs agentic — picked by dominant production failure mode (literature: combining with routing wins, [arXiv:2502.11371](https://arxiv.org/abs/2502.11371)) · Qdrant tiered multitenancy for a dominant tenant.

### Explicit non-goals

- Cross-org federation (GAP-FED-01).
- Collection-per-tenant; `m=0/payload_m` per-tenant HNSW (blocks cross-org queries).
- Assertion/confidence labels in the UI (CHI 2024 / ACL 2024-25 evidence: damages trust).
- Autonomous taxonomy mutation without human approval.
- Evidence weights in served ordering before eval + canary proof.
- Personal LLM/Graphiti enrichment changes before the legal/product decision.
- A separate PII microservice for chat guardrails (LLM-based guardrails already shipped); Presidio only as a building block inside the transcript-gap pipeline.
- Replacing the reranker; query decomposition as default path; per-tenant synthesis-model override; GraphRAG as blanket replacement for focused retrieval.

---

## 5. Verification Plan

### Mainline / adversarial review gate

For every next code, migration, alerting, scheduler, data-contract, or production
ops change from this plan:

1. Implement and run local verification.
2. Produce a focused adversarial-review prompt that names the exact commits/files,
   expected behavior, verification already run, and the highest-risk failure modes.
3. Get an independent review result, or an explicit user waiver, before pushing to
   `main`.
4. If review finds issues, fix them and re-run verification before push.

Docs-only status edits can land with normal diff review, but any doc edit that
claims a production behavior changed must point to code evidence and tests.

### Commands / tests

```bash
# per service quality gate (both ruff commands — known CI pitfall)
uv run ruff check . && uv run ruff format --check .

# targeted suites
pytest klai-retrieval-api/tests
pytest klai-knowledge-ingest/tests/eval
pytest klai-knowledge-mcp/tests
pytest klai-portal/backend/tests -k "gap"

# eval ad hoc
docker exec klai-core-knowledge-ingest-1 \
  python -m knowledge_ingest.eval --suite chat --variant <name>

# gate corpus
python klai-retrieval-api/scripts/generate_gate_reference.py

# migration hygiene (multi-PR head split pitfall)
alembic heads   # must be 1, per service
```

New named tests (merged v1+v2):

- `test_temporal_validity_filter_supports_current_and_legacy_payloads` (exists in spirit — keep green)
- `test_reingest_supersede_excludes_old_chunks` (A1 integration test — the key new one)
- `test_ragas_suite_requires_reference_answer_for_scored_query`
- `test_expected_chunks_canary_fails_when_missing`
- `test_mcp_search_knowledge_scope_parameter_maps_to_retrieve_body`
- `test_personal_kb_does_not_enqueue_enrichment_when_policy_disabled`
- `test_personal_kb_does_not_enqueue_graphiti_when_policy_disabled`
- `test_qdrant_org_id_index_reports_tenant_status`
- `test_reconcile_pg_qdrant_logs_failed_status_on_discrepancy`
- `test_pg_qdrant_reconcile_alert_rule_present`
- gap-loop: semantic dedup threshold tests, lifecycle transitions, reopen-after-resolved, mocked judge classification, off/shadow/full telemetry modes
- transcript arm: untrusted-input handling, PII redaction, delete/offboarding cascade

### RAGAS variants

`baseline-v5` (after D1 — new zero point) → `evidence_tier_full`, `evidence_tier_temporal_only` (G1: 7 nights, Wilcoxon p<0.05, ≥+0.02 precision AND faithfulness) → `gate_active_v1` (B1) → taxonomy on/off (F3 pilot) → hook-path vs direct (D3) → strict vs open mode → NL/EN splits. Canary metric (D2) runs in **every** variant.

### Logs / metrics (VictoriaLogs / Grafana)

- Retrieval: `retrieval_decision_record` — `gate_would_bypass`/`gate_bypassed`/`gate_margin` (per language/tenant), `router_layer_used`, `expanded_in_top_k`, `confidence_band`, `shadow_eval` order-diffs; new p50/p95-per-step panel.
- Ingest: `ingest_complete`, `enrichment_enqueue_skipped` (will carry the personal carve-out reason), `enrichment_complete`, `enrichment_infra_failed`, `graphiti_episode_started`, `graphiti_aborted_artifact_missing`.
- Consistency: `pg_qdrant_reconcile` event + `pg_qdrant_reconcile_failed` alert on discrepancy > 0 / job error + `pg_qdrant_reconcile_missing` dead-man alert; Qdrant payload schema snapshot for `org_id.is_tenant`; embedding-queue depth/age/dead-letters once H2 lands.
- Gap loop: dedup merge rate, judge verdict distribution, editor accept/dismiss rate, reopened rate, transcript-arm candidates per transcript + PII-redaction hit rate.
- Existing guardrails stay: `rag-quality` dashboard + `rag_eval_faithfulness_low` (<0.80) alert.

### Browser / UI checks (only where UI changes land)

Preflight: `scripts/local-dev-status.sh --mode local --strict`. Flows: gap inbox list/filter/resolve/reopen (E4), taxonomy coverage page (F1), AI-draft flow (E5), MCP tools via a real LibreChat session.

### Production trace gates

For production-affecting retrieval changes: pick request IDs → VictoriaLogs `request_id:<uuid>` → verify hook request body → `retrieval_decision_record` → EvidencePack → final citation rendering. Every activation (gate, evidence, router) gets ≥7 days shadow/telemetry before cutover; rollback = config flag.

---

## 6. Online Research Used (verified URLs)

**Qdrant multitenancy & ops:** [multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/) · [indexing (is_tenant, is_principal, datetime index)](https://qdrant.tech/documentation/manage-data/indexing/) · [1.11 release](https://qdrant.tech/blog/qdrant-1.11.x/) · [1.16 tiered multitenancy](https://qdrant.tech/blog/qdrant-1.16.x/) · [FAQ (zero-downtime reindex)](https://qdrant.tech/documentation/faq/qdrant-fundamentals/) · [filtering & cardinality](https://qdrant.tech/articles/vector-search-filtering/) — conclusion: tenant index + datetime index + filter-field audit; tiered only for dominant tenants; no official quantitative benchmarks.

**RAGAS / eval:** [context_recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/) · [context_precision (incl. ID-based)](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/) · [testset generation](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/) · [LLM eval statistics](https://arxiv.org/html/2506.13023v1) — conclusion: reference answers required; ID-based canaries native; ~250 samples; Wilcoxon.

**Gating / routing:** [Adaptive-RAG](https://arxiv.org/abs/2403.14403) · [Self-RAG](https://arxiv.org/abs/2310.11511) · [TARG](https://arxiv.org/abs/2511.09803) · [LlamaIndex routers](https://developers.llamaindex.ai/python/framework/module_guides/querying/router/) · [LiteLLM auto-routing](https://docs.litellm.ai/docs/proxy/auto_routing) · [RouteLLM](https://github.com/lm-sys/RouteLLM) — conclusion: embedding-margin gate fine as v1, LiteLLM native complexity router worth a shadow pilot.

**GraphRAG / graph:** [GraphRAG DRIFT](https://microsoft.github.io/graphrag/query/drift_search/) · [Graphiti](https://github.com/getzep/graphiti) · [Zep paper](https://arxiv.org/abs/2501.13956) (vendor-authored) · [When to use graphs in RAG](https://arxiv.org/abs/2506.05690) · [RAG vs GraphRAG systematic eval](https://arxiv.org/abs/2502.11371) · [LightRAG](https://arxiv.org/abs/2410.05779) — conclusion: route graph leg by query class; measure contribution; don't expand graph work preemptively.

**Judging / dedup:** [Evidently LLM-as-judge](https://www.evidentlyai.com/llm-guide/llm-as-a-judge) · [NeMo SemDedup](https://docs.nvidia.com/nemo-framework/user-guide/24.09/datacuration/semdedup.html) — conclusion: strict rubric, low-cardinality labels, calibrate on hand labels; embed+cluster+threshold dedup.

**Grounding / guardrails:** [Anthropic Citations API](https://platform.claude.com/docs/en/docs/build-with-claude/citations) · [Azure groundedness detection](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/groundedness) · [Vectara HHEM-2.1-Open](https://huggingface.co/vectara/hallucination_evaluation_model) — conclusion: Klai's deterministic EvidencePack matches the industry pattern; an HHEM-style post-generation faithfulness gate is a candidate strict-mode addition (cheap, CPU).

**Privacy:** [EDPB Opinion 28/2024](https://www.edpb.europa.eu/news/news/2024/edpb-opinion-ai-models-gdpr-principles-support-responsible-ai_en) · [Hamburg DPA discussion paper](https://datenschutz-hamburg.de/fileadmin/user_upload/HmbBfDI/Datenschutz/Informationen/240715_Discussion_Paper_Hamburg_DPA_KI_Models.pdf) · [GDPR Art. 5](https://gdpr-info.eu/art-5-gdpr/) · [Presidio](https://microsoft.github.io/presidio/) — conclusion: LLM enrichment is its own processing purpose needing its own necessity test; embedding-only carve-out is a defensible minimization measure documented in your own DPIA (no DPA text blesses the pattern by name).

**Not verified despite research:** official Qdrant benchmarks for is_tenant; LiteLLM router version + content-array bug status; ~83% judge-agreement claim; agentic-RAG cost figures; completeness of `upgrade_org_id_tenant_index.py`; deployed Qdrant server version on core-01.

---

## 7. Evidence Split

**Proven (repo/docs/code, verified 2026-06-11):** all **[code ✓]** claims above with file:line, including the five backlog shifts (§0); LOOP/PROV/SYNC/TAX/MCP/PRIV/EVAL/ROUTE gaps re-confirmed as described; EvidencePack/citation contract; eval harness mechanics; transcript ingest path.

**Inferred:** stub gate corpus is why shadow telemetry is currently uninformative; in-process cosine matching suffices for gap-dedup volumes; is_tenant migration has limited-but-positive latency impact at current data volumes; existing prod collection still lacks the tenant index (strongly implied by the code comment; verify live).

**Needs online research:** done — see §6 with verified URLs and the explicit not-verified list.

**Needs product/legal decision:** GAP-PRIV-01 (keep carve-out vs amend doc + legal basis; plus cleanup of already-enriched personal content); transcript→gap per-tenant opt-in, retention, redaction policy, and user-visible disclosure; accepting evidence-tier "flags-off" if nobody runs the A/B; editorial workflow ownership for the gap inbox.

**Not verified:** live Qdrant payload schema / server version on core-01; live gate shadow false-bypass rate; live tenant telemetry levels; production RAGAS results beyond the documented 2026-05-05 snapshot. *(Resolved since v2 first draft: re-ingest delete behavior — both upsert paths delete-by-path before upserting **[code ✓ 2026-06-11]**; the "scribe-api summarizes before ingest" doc claim — falsified, the full transcript text is ingested (`knowledge_adapter.ingest_scribe_transcript` sends `full_text`); both corrections are now reflected in the architecture docs.)*

---

## Confidence

**88/100** — every load-bearing claim carries a same-day file:line verification or a WebFetch-verified URL; remaining uncertainty is concentrated in live production state (Qdrant server config, runtime telemetry) and the four explicitly flagged not-verified items. The v1 plan (82/100) is preserved alongside; its stale chunk_type items were dropped, its operational details (verifier, shadow-first rollouts, richer schemas, named tests, log event names) were adopted.
