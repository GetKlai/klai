# Product Gaps Backlog — where the docs describe more than the code does

> Created 2026-06-08 from a doc-vs-code drift audit of `docs/architecture/`.
> **Updated 2026-06-11** after per-gap re-verification against source: five entries
> shifted — `GAP-TEMPORAL-01` is fixed on the serving path (filter + delete-then-upsert),
> `GAP-RETR-01` and `GAP-TENANCY-01` are partially closed (shadow gate + stub corpus;
> `is_tenant` for new collections, existing collection needs the one-time upgrade script),
> `GAP-EVID-01` shifted (conservative weights exist, shadow-gated; the deciding A/B was
> never built), and `GAP-INGEST-02` is **closed by removal**. See
> [`knowledge-rag-improvement-plan.md`](knowledge-rag-improvement-plan.md) for the plan
> built on this refreshed state.
> This file captures **Type-B drift**: places where an architecture document
> describes a richer / more capable design than what is actually implemented
> today. These are **product-improvement opportunities**, not doc bugs — the
> ambition was deliberately written down, so we keep it here as a backlog
> instead of flattening it out of the architecture docs.
>
> The architecture docs themselves now carry a short **"Intended design vs.
> current implementation"** callout at each affected section, pointing back to
> the relevant gap ID below.

## How to read this

- **Gap ID** — stable handle (`GAP-<area>-NN`) referenced from the architecture docs.
- **Verification** — `✓✓` workflow-verified *and* re-checked by hand against
  source; `✓` adversarially verified in the audit workflow; `·` evidenced with
  `file:line` but not independently re-verified.
- **Effort** — rough order of magnitude only (S / M / L / XL). Not a commitment.
- Evidence paths are relative to repo root and were accurate as of 2026-06-08.

## ~~Not a gap — a real bug~~ — FIXED on the serving path (2026-06-11)

`GAP-TEMPORAL-01` is resolved as a serving bug. Verified against source 2026-06-11:

- Retrieval now uses a **dual-contract** temporal filter
  (`search.py::_temporal_validity_filter`): `must_not` over both the legacy
  `valid_at`/`invalid_at` fields and the ingest-written `valid_from`/`valid_until`
  epoch fields (open-ended sentinel handled). Integration-tested in
  `klai-retrieval-api/tests/test_search.py` (expired/future/active/legacy cases).
- The retro's open question ("do stale points linger on re-ingest?") is answered:
  **both** `qdrant_store.upsert_chunks` and `upsert_enriched_chunks` delete all
  existing points for `(org_id, kb_slug, path)` before upserting, so same-path
  re-ingest physically removes superseded chunks. Page deletes call
  `delete_document()`.

Remaining (folded into other gaps, not a temporal bug):
- `soft_delete_artifact()` still updates PG only (`belief_time_end`); Qdrant
  hygiene relies on the delete-then-upsert above. If the Qdrant delete fails
  after PG commit, divergence is silent — that is `GAP-SYNC-01`.
- `valid_from`/`valid_until` are in every query's `must_not` but have **no
  payload index** (Qdrant supports datetime/integer indexes; `is_principal`
  exists for exactly this) — latency risk at scale, see the improvement plan.

History: [`docs/retros/2026-06-08-temporal-filter-field-mismatch.md`](../retros/2026-06-08-temporal-filter-field-mismatch.md)
(carries a 2026-06-11 status addendum).

---

## Priority view

### Quick wins (S effort, high value — close to config changes)

| Gap | One-liner (state per 2026-06-11) | Verif |
|---|---|---|
| `GAP-RETR-01` | Gate now runs in **shadow mode** with a 16-line stub corpus → never bypasses in practice; remaining work = generate the full 200-query corpus + analyze `gate_would_bypass` telemetry + activate | ✓✓ 06-11 |
| `GAP-TENANCY-01` | `is_tenant=True` ships for **new** collections; the existing prod collection still needs the one-time online upgrade (`scripts/upgrade_org_id_tenant_index.py`) | ✓✓ 06-11 |
| `GAP-EVID-01` | Assertion-mode weights are no longer flat (conservative profile, shadow-gated); the deciding RAGAS A/B (SPEC-EVIDENCE-001-FOLLOWUP-001) was **never built** and its 30-day deadline lapsed | ✓✓ 06-11 |
| `GAP-MCP-01` | MCP `search_knowledge` hardcodes `scope: "both"` (no org-only / personal-only / type / time filters) | ✓✓ |
| ~~`GAP-INGEST-02`~~ | **CLOSED** — `chunk_type` classification removed 2026-06-08 (`enrichment.py`, rationale in `docs/research/chunk-type-retrieval-value.md`) | ✓✓ 06-11 |
| ~~`GAP-TEMPORAL-01`~~ | **FIXED** on the serving path — dual-contract filter + delete-then-upsert; see section above | ✓✓ 06-11 |

### Flagship differentiator (L, the "self-improving knowledge base" story)

`GAP-LOOP-01..06` — the gap-detection + editorial improvement engine is the
single largest ambition-vs-reality delta. The editorial inbox UI and threshold
detection exist; the value is in the LLM judge, semantic dedup, lifecycle, and
the transcript signal.

### Big bets (L/XL, core differentiators)

| Gap | One-liner |
|---|---|
| `GAP-PROV-01` | Provenance DAG / entity registry / supersession is schema-only (0 INSERTs) → invalidation cascade + confidence calibration cannot fire |
| `GAP-TAX-01` | Taxonomy never re-clusters (task registered but never scheduled) → not self-maintaining |
| `GAP-SYNC-01` | Dual-store outbox (`embedding_queue`) + nightly reconciliation is schema-only → no crash-safe PG↔Qdrant sync |

### Sensitive — compliance

| Gap | One-liner |
|---|---|
| `GAP-PRIV-01` | Personal knowledge runs the full LLM enrichment + Graphiti pipeline the doc says it must *not* until a legal basis exists |

---

## Cluster 1 — Self-improving loop & gap detection (§8, §12)

The doc sells a sophisticated editorial knowledge-improvement engine. The code
is a threshold classifier writing an exact-string event log to Postgres.

### GAP-LOOP-01 — Phase-2 LLM-as-judge does not exist  ·  L  ·  ✓
- **Intended (§8.2):** 2-phase pipeline; Phase 2 is an LLM-as-judge (Claude
  Haiku) over the knowledge item + top-3 chunks producing
  `{verdict: covered|partial|new, missing_aspects: [...], article_id}`.
- **Reality:** `classify_gap()` is a pure no-I/O function returning only `hard`
  (no chunks) / `soft` (all reranker scores below threshold) / `None`. No LLM,
  no `covered|partial|new`, no `missing_aspects`.
- **Why it matters:** editors get "this query scored low" instead of "partially
  covered by article X, missing aspect Y". The `missing_aspects` output is the
  labour-saving core of the feature.
- **Evidence:** `deploy/litellm/klai_retrieval_telemetry.py:84-115`;
  `klai-portal/backend/app/services/gap_classification.py:13-33`.

### GAP-LOOP-02 — No semantic gap registry (exact-string dedup)  ·  L  ·  ✓
- **Intended (§8.2):** a Qdrant `org_{uuid}_gap_registry` where new gaps are
  BGE-M3 embedded and hybrid-ANN matched, incrementing `frequency` / `last_seen`
  on a semantic match (paraphrases collapse to one entry).
- **Reality:** one Postgres `INSERT` per occurrence; "frequency" is a read-time
  `COUNT() GROUP BY query_text` — exact-string only. No embedding column, no
  Qdrant gap collection.
- **Why it matters:** a high-demand gap asked 20 different ways looks like 20
  low-frequency gaps and never rises in the inbox. Prioritisation structurally
  undercounts real demand.
- **Evidence:** `klai-portal/backend/app/models/retrieval_gaps.py:22-54`;
  `klai-portal/backend/app/api/internal.py:1228`;
  `klai-portal/backend/app/api/app_gaps.py:74-91`.

### GAP-LOOP-03 — No gap lifecycle / no article back-link  ·  L  ·  ✓
- **Intended (§8.3):** `open → in_progress → resolved`, a `resolving_article_id`
  back-link, and re-open after ≥3 reappearances with full history (worked
  example G47).
- **Reality:** one nullable `resolved_at` timestamp. A reappearing query is a
  brand-new row. No `in_progress`, no `resolving_article_id`, no re-open.
- **Why it matters:** editors cannot see which article fixed which gap, cannot
  mark work-in-progress, and a weak fix spawns disconnected rows instead of
  re-opening the original.
- **Evidence:** `klai-portal/backend/app/models/retrieval_gaps.py:53`;
  `klai-portal/backend/app/services/gap_rescorer.py:144-156`.

### GAP-LOOP-04 — Editorial priority is frequency-only  ·  M  ·  ✓
- **Intended (§8.4):** `priority_score = frequency × urgency_weight ×
  recency_factor` (urgency 2.0 for error-code, 1.5 for escalation).
- **Reality:** `frequency_per_day` buckets only; list endpoint orders by raw
  `COUNT desc`. No urgency, no recency.
- **Why it matters:** a frequent trivial question outranks a rare but
  production-breaking error-code gap.
- **Evidence:** `klai-portal/backend/app/api/app_gaps.py:89,197-204`.

### GAP-LOOP-05 — Only 1 of 3 gap-trigger signals  ·  L  ·  ✓
- **Intended (§8/§12):** gaps from (1) transcript unanswered-questions, (2)
  conversations marked `resolved:false`, (3) low retrieval confidence.
- **Reality:** only signal #3 (low-confidence chat retrieval) feeds the registry.
  No transcript / unresolved-ticket path.
- **Why it matters:** the richest gaps — real support conversations where the
  agent couldn't answer — never surface. The loop only improves topics users
  already tried to look up in chat.
- **Evidence:** `deploy/litellm/klai_retrieval_telemetry.py:181-244`;
  `klai-portal/backend/app/api/internal.py:1191-1240`.

### GAP-LOOP-06 — No AI-draft affordance in the editorial inbox  ·  L  ·  ·
- **Intended (§12):** "Human writes **or AI drafts** new/updated article" —
  humans decide *what* to write, not *whether*.
- **Reality:** pure human authoring in the BlockNote editor → Gitea. No
  gap→draft generator (`grep 'draft'` over `klai-portal/backend/app` = 0).
- **Why it matters:** AI-assisted drafting is what makes the loop scale; without
  it every prioritised gap still needs full manual authoring.
- **Evidence:** `klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx`;
  `klai-portal/backend/app/services/docs_client.py`.

## Cluster 2 — Provenance, supersession & dual-store integrity (§3, §5)

### GAP-PROV-01 — Provenance DAG / entity registry / supersession is partial  ·  L  ·  ⊙
- **Intended (§3.3, §5):** a `derivations` adjacency DAG, a `knowledge.entities`
  registry + `artifact_entities`, and a `superseded_by` chain — powering an
  invalidation cascade ("retract src-2847 → flag everything derived from it")
  and confidence-from-source-count, plus `WITH RECURSIVE` lineage analytics.
- **Reality:** all four structures exist in `0001_baseline.py`.
  `derivations` is now populated for Docs artifacts when valid `derived_from`
  UUIDs are present, but production code still has **0 INSERTs** into
  `entities` / `artifact_entities`; `superseded_by` is only ever set to `NULL`.
  Entity data that *is* produced (Graphiti) lives in FalkorDB + the Qdrant
  payload, not the PG registry the doc's SQL queries.
- **Why it matters:** the cascade and confidence calibration the doc presents as
  *enabled* only have partial inputs; queries over derivations can work for
  explicitly linked Docs artifacts, but entity/supersession analytics still have
  no populated PG inputs.
- **Evidence:** `klai-knowledge-ingest/alembic/versions/0001_baseline.py:115,144-150,213-219,236-250`;
  `klai-knowledge-ingest/knowledge_ingest/pg_store.py` writes `knowledge.derivations`
  for valid `derived_from` parents, while `superseded_by` remains only cleared.

### GAP-PROV-02 — `derived_from` wired for Docs, `confidence` a manual label  ·  L  ·  ·
- **Intended (§3.3):** every artifact keeps a `derived_from` chain (source+span)
  and a `confidence` derived from independent-source count (example: 0.87 from 3
  sources).
- **Reality:** `search_knowledge` exposes source `artifact_id`, `save_to_docs`
  accepts `derived_from`, and ingest writes valid parent→child rows to
  `knowledge.derivations`. The editor display, delete-warning flow, automatic
  source capture, and confidence-from-source-count remain open. `confidence` is
  still a `high/medium/low` frontmatter enum, never computed and never read in
  retrieval scoring.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:177,214-215,541`.

### GAP-SYNC-01 — Dual-store outbox + reconciliation is schema-only  ·  L  ·  ⊙
- **Intended (§5):** transactional outbox — PG write (incl. `embedding_queue`
  row) → worker → Qdrant upsert → mark processed; retry from PG on failure;
  nightly bidirectional reconciliation.
- **Reality:** `knowledge.embedding_queue` exists but has **0 INSERTs** (every
  reference is a DELETE cleanup). The write path is synchronous PG-then-Qdrant in
  the same handler; a Qdrant failure after PG commit leaves silent divergence
  with no queue to recover from. The only "reconcile" in the tree is crawl
  stale-path reconciliation (SPEC-INGEST-RECONCILE-001), not PG↔Qdrant.
- **Why it matters:** this codebase already has a history of silent PG↔Qdrant
  divergence on connector deletes; the documented outbox is exactly the
  crash-safety that's missing.
- **Evidence:** `0001_baseline.py:222-233,599-602`;
  `routes/ingest.py:644-672`; 0 INSERTs (verified by hand).

## Cluster 3 — Epistemic / evidence scoring (§3.2, §7.4)

### GAP-EVID-01 — Assertion-mode weights exist but the deciding A/B was never run  ·  S  ·  ✓✓ (updated 2026-06-11)
- **Intended (§7.4):** assertion-mode is one of four multiplicative scoring
  dimensions (`final = reranker × content_type × assertion_mode × temporal ×
  pagerank`) with its own default-true flag.
- **Reality (2026-06-11):** `_assertion_weight()` now reads a **conservative
  profile** from `DEFAULT_EVIDENCE_PROFILE["assertion_mode_weights"]`
  (factual/procedural 1.00, hypothesis 0.90, unknown 0.97 — spread 0.10 per the
  weights research). Everything stays **shadow-gated** (`EVIDENCE_SHADOW_MODE=true`):
  computed and logged, never served. The real gap has moved: the RAGAS A/B that
  SPEC-EVIDENCE-001-FOLLOWUP-001 requires to decide activate / temporal-only /
  decommission / flags-off (variants `evidence_tier_full`,
  `evidence_tier_temporal_only`) **does not exist in the eval harness** — only
  `baseline` is ever used — and the SPEC's 30-day deadline has lapsed while the
  shadow `deepcopy + apply()` CPU cost is paid on every request.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/evidence_tier.py:43-67,125-148`;
  `klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py:38-51` (variant env
  read, no evidence_tier variants defined anywhere).

### GAP-EVID-02 — 3-into-5 epistemic grouping unbuilt  ·  M  ·  ✓
- **Intended (§3.2):** the 5 epistemic values collapse to 3 groups
  (assertion / speculation / procedure) for scoring, with `unknown` neutral.
- **Reality:** no such grouping exists anywhere in code; assertion-mode is a
  pass-through with no effect on ranked order.
- **Why it matters:** surfacing verified facts above speculation is what makes
  "AI serves claims, cites sources" trustworthy. Today a hypothesis chunk and a
  factual chunk compete on identical footing.
- **Evidence:** `evidence_tier.py:113-116`; no grouping in
  `taxonomy_classifier.py` / `taxonomy_lookup.py`.

## Cluster 4 — Taxonomy (§6)

### GAP-TAX-01 — Taxonomy never re-clusters (not self-maintaining)  ·  M  ·  ✓✓
- **Intended (§6.4-6.5):** ongoing BERTopic/HDBSCAN discovery, monthly
  monitoring (outlier rate, coverage, velocity), outlier alerts, merge
  suggestions.
- **Reality:** no periodic re-clustering task is registered; only the manual
  admin "bootstrap" endpoint populates a taxonomy. A KB's taxonomy is frozen at
  bootstrap; new docs are classified against the frozen set but never trigger
  new-node proposals. No outlier/velocity monitoring, no merge surfacing.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/routes/taxonomy.py`
  calls `proposal_generator.generate_bootstrap_proposals_v2(...)` only from the
  manual bootstrap endpoint.
  No other taxonomy re-cluster enqueue or scheduler exists; verified by hand.

### GAP-TAX-02 — `coverage` is binary, not fraction-classified  ·  M  ·  ·
- **Intended (§6.7):** coverage = "% of corpus assigned to non-noise clusters".
- **Reality:** `get_kb_taxonomy_coverage` returns `1.0` iff the KB has ≥1 node
  else `0.0`. A KB with 1 node and 10 000 untagged chunks scores identical
  coverage to a fully tagged KB; the query-time gate can't tell them apart.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/taxonomy_lookup.py:15-18,66-78,133,158-160`;
  `deploy/litellm/klai_knowledge.py:611-621`.

### GAP-TAX-03 — Taxonomy classification is write-only for chat retrieval  ·  L  ·  ·
- **Intended:** per-document taxonomy classification narrows retrieval.
- **Reality:** retrieval applies `taxonomy_node_ids`/`tags` only as
  request-supplied filters; the automatic LiteLLM-hook path never sends them (it
  reads `taxonomy_node_ids` only to attach to a gap event). The classification
  cost is paid at ingest but the primary retrieval path ignores it — and with 0
  curated nodes the whole apparatus no-ops anyway (the roadmap's `taxonomy_v1`).
- **Evidence:** `routes/ingest.py:430-495`;
  `klai-retrieval-api/retrieval_api/services/search.py:195-214`;
  `deploy/litellm/klai_knowledge.py:968`.

## Cluster 5 — Retrieval gate & routing

### GAP-RETR-01 — Gate in shadow mode with a stub corpus  ·  S  ·  ✓✓ (updated 2026-06-11)
- **Intended (knowledge-retrieval-flow §Gate):** a semantic gate that skips KB
  lookup for general-knowledge queries (`RETRIEVAL_GATE_ENABLED=true`).
- **Reality (2026-06-11):** materially better than the 06-08 claim, but still
  not bypassing in practice. `data/gate_reference.jsonl` **exists** — a 16-line
  stub of meta/utility queries (titles, translate, summarize in NL/EN), not the
  200-query × 6-language corpus `scripts/generate_gate_reference.py` produces.
  The gate now also runs in **shadow mode by default**
  (`retrieval_gate_shadow=True`, `config.py`): it computes and logs
  `gate_would_bypass` per request but never acts. Strict KB mode skips the gate
  entirely by design (`gate_skipped_reason=strict_mode`, `retrieve.py`).
- **Why it matters:** every query still pays the full embed + multi-leg + rerank
  pipeline. Closing the gap = (1) generate + commit the full corpus, (2) analyze
  1–2 weeks of `gate_would_bypass` telemetry (false-bypass rate per language),
  (3) flip `retrieval_gate_shadow=false`.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/gate.py`;
  `retrieval_api/config.py:21-28`; `retrieval_api/data/gate_reference.jsonl`
  (16 lines); `retrieve.py` strict-mode gate block; `tests/test_gate.py`
  (incl. shadow-mode tests).

### GAP-ROUTE-01 — "Complexity Router" is a 3-signal heuristic  ·  L  ·  ✓✓
- **Intended (platform.md):** LiteLLM's native Complexity Router scoring every
  query on 7 dimensions across 4 tiers.
- **Reality:** a custom callback (`custom_router.py`) with 3 signals — a
  `role=tool` message, last-user-message tokens > 300, ≥3 URLs (+ a 3000-token
  safety net). No native auto-router config; no code/reasoning/technical-term
  scoring.
- **Why it matters:** mis-routes common cases — a short but genuinely complex
  reasoning question goes to the fast model; a long trivial paste goes to the
  expensive one.
- **Evidence:** `deploy/litellm/custom_router.py:36-44,182-220`;
  `deploy/litellm/config.yaml:69` (`simple-shuffle`), `:85-87` (callback).

### GAP-ROUTE-02 — Router never selects `klai-medium`  ·  M  ·  ✓
- **Intended:** routing across 4 tiers by complexity.
- **Reality:** the router only ever outputs `klai-large` / `klai-fast` /
  unchanged `klai-primary`; `klai-medium` is reachable only as a quota fallback
  or a manual alias. The middle tier is dead for routing, so every borderline
  query is forced to an extreme.
- **Evidence:** `deploy/litellm/custom_router.py:164-166,186-220`;
  `deploy/litellm/config.yaml:40-47,73`.

### GAP-ROUTE-03 — Router Layer-3 LLM fallback is dead code  ·  M  ·  ·
- **Intended:** an optional LLM tie-breaker (`router_layer_used='llm'`) when
  Layers 1+2 are inconclusive.
- **Reality:** the LLM branch is guarded by `if llm_fallback and llm_fn:`, but
  the only caller never passes `llm_fn` (defaults `None`), and
  `router_llm_fallback` defaults `False`. The branch can never run; when keyword
  + centroid routing are both inconclusive the router silently returns no
  selection.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/router.py:249,291`;
  `retrieve.py:260-269`; `config.py:88`.

## Cluster 6 — Multitenancy & scale (§5, §10)

### GAP-TENANCY-01 — `is_tenant` ships for new collections; existing collection needs the one-time upgrade  ·  S  ·  ✓✓ (updated 2026-06-11)
- **Intended (§5.1):** `org_id` payload index with `is_tenant: true` (Qdrant 1.11+
  native multitenancy → tenant co-location, no recall degradation) plus
  Qdrant 1.16 tiered sharding for large tenants.
- **Reality (2026-06-11):** `ensure_collection()` now creates the `org_id` index
  with `KeywordIndexParams(is_tenant=True)` — but only when the index doesn't
  exist yet, i.e. **new collections**. The code comment is explicit: existing
  collections keep their plain keyword index until upgraded once, online, via
  `scripts/upgrade_org_id_tenant_index.py` (deliberately not auto-rebuilt at
  startup). Whether the production `klai_knowledge` collection has been upgraded
  is **not verifiable from the repo** — check the live payload schema.
- **Why it matters:** the recall/scale guarantee the doc cites as the reason
  single-collection multitenancy is acceptable only holds once the prod
  collection is upgraded. Qdrant supports a zero-downtime online reindex
  (serve old index until the new one is built).
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:85-102`;
  `klai-knowledge-ingest/scripts/upgrade_org_id_tenant_index.py`.

### GAP-FED-01 — Federated / cross-org knowledge unbuilt
- Tracked as an open question in §13 — listed here only as a pointer; it is
  explicitly future work, not a silent gap.

## Cluster 7 — AI interface (§9)

### GAP-MCP-01 — MCP read surface is 1 of 5 tools  ·  L  ·  ✓✓
- **Intended (§9.2):** five read tools — `search(query, scope, type?,
  time_range?)`, `related_concepts`, `belief_evolution`, `provenance_chain`,
  `recent`.
- **Reality:** only `search_knowledge` exists, and it hardcodes `scope: "both"`
  and exposes only `(query, top_k)` — no scope/type/time_range. The other four
  tools exist only in docs. External agents can only do flat semantic search.
- **Why it matters:** the missing tools are exactly the ones that expose the
  knowledge model's depth (provenance, temporal, graph) to an agent.
- **Evidence:** `klai-knowledge-mcp/main.py:1055,1090`; verified by hand.

## Cluster 8 — Privacy carve-out (§10)

### GAP-PRIV-01 — Personal knowledge runs the full LLM pipeline  ·  S  ·  ⊙
- **Intended (§10):** personal knowledge is BGE-M3-direct-embedding only — *not*
  processed by contextual/HyPE enrichment or any cloud LLM "until the legal basis
  is formally established".
- **Reality:** the enqueue path gates enrichment only on chunk count + org config;
  `enrichment_policy.py` has **no** `user_id`/personal branch. Personal artifacts
  carry `chunk_user_id` but it never gates enrichment or Graphiti. Personal notes
  go through the identical contextual-prefix + HyPE + Graphiti pipeline as org
  knowledge.
- **Why it matters:** this is the one enrichment distinction framed as a
  compliance boundary. A one-line scope check in the enqueue path would honour the
  documented privacy posture. **Validate with legal/privacy before assuming the
  doc's carve-out is still the intended policy.**
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:637-728`;
  `enrichment_policy.py:18-40` (no personal branch); verified by hand.

## Cluster 9 — Measurement & eval harness (roadmap, ingest)

### GAP-EVAL-01 — CLOSED in code: RAGAS uses full reference answers  ·  M  ·  ✓✓ (closed 2026-06-11)
- **Intended (roadmap):** headline `context_precision +61%` / `context_recall
  +154%` measured by a RAGAS harness over hand-curated Voys queries.
- **Resolution (2026-06-11):** scored suite runs now call
  `load_suite(..., require_reference_answer=True)`, every shipped query in
  `chat`, `knowledge_org`, and `_sample` has `reference_answer`, and
  `judge_client.evaluate_query()` accepts only that full reference answer for
  RAGAS context metrics. There is no implicit fallback to `expected_topics`.
- **Remaining operational step:** old baseline rows are not comparable. Run a
  manual canary/debug eval first, then recapture `baseline-v5`.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/eval/suite_loader.py`;
  `eval/ragas_runner.py`; `eval/judge_client.py`; `eval/suites/chat.yaml`;
  `eval/suites/knowledge_org.yaml`; `tests/eval/test_seed_suites.py`;
  `tests/eval/test_judge_client.py`.

### GAP-EVAL-02 — CLOSED in code: regression canaries hard-fail  ·  S  ·  ✓✓ (closed 2026-06-11)
- **Intended (roadmap):** 9 `easy_lookup` "regression canaries" with
  `expected_chunks` populated.
- **Resolution (2026-06-11):** `ragas_runner` compares `expected_chunks` before
  calling the judge. Missing canaries write a NULL-metric row with
  `meta.canary.passed=false`, skip fuzzy RAGAS scoring, and trigger the new
  `rag_eval_canary_dropped` Grafana alert. The matcher uses strong fields for
  short markers and only permits body-text matches for longer phrase markers.
- **Remaining operational step:** because these markers were dormant before,
  verify a live `manual-canary-debug` run before relying on the HIGH alert.
- **Evidence:** `eval/ragas_runner.py`; `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml`;
  `docs/runbooks/rag-quality.md`; `tests/eval/test_ragas_runner.py`;
  `tests/eval/test_grafana_assets.py`.

### GAP-INGEST-02 — CLOSED: `chunk_type` removed  ·  —  ·  ✓✓ (closed 2026-06-08, verified 2026-06-11)
- **Was:** ingest paid a per-chunk LLM classification
  (procedural|conceptual|reference|warning|example) + a strict-Literal retry
  round-trip, stored to the Qdrant payload, with zero retrieval consumers.
- **Resolution:** the classification was **removed** on 2026-06-08 — the gap was
  closed by deleting the cost, not by wiring a consumer. Rationale recorded in
  `docs/research/chunk-type-retrieval-value.md`. Document-level `content_type`
  (consumed by evidence-tier) is unaffected.
- **If revisited:** any future chunk-type-aware retrieval SPEC should start from
  that research doc, not from re-adding the label speculatively.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/enrichment.py:10-15`
  (removal note); 0 `chunk_type` references left in `retrieval_api` or
  `deploy/litellm`.

---

## Appendix — gap index

| ID | Area | Effort | Verif | Doc |
|---|---|---|---|---|
| ~~GAP-TEMPORAL-01~~ | temporal filter — **FIXED 2026-06-11** (serving path) | — | ✓✓ | knowledge-architecture §5 / retrieval-flow |
| GAP-LOOP-01 | gap LLM-judge | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-02 | semantic gap registry | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-03 | gap lifecycle | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-04 | editorial priority | M | ✓ | knowledge-architecture §8 |
| GAP-LOOP-05 | gap trigger signals | L | ✓ | knowledge-architecture §8,§12 |
| GAP-LOOP-06 | AI-draft | L | · | knowledge-architecture §12 |
| GAP-PROV-01 | provenance DAG/entities | L | ⊙ | knowledge-architecture §3,§5 |
| GAP-PROV-02 | derived_from/confidence | L | · | knowledge-architecture §3 |
| GAP-SYNC-01 | dual-store outbox | L | ⊙ | knowledge-architecture §5 |
| GAP-EVID-01 | evidence-tier A/B never run (weights exist, shadow-gated) | S/M | ✓✓ | knowledge-architecture §7.4 |
| GAP-EVID-02 | 3-into-5 grouping | M | ✓ | knowledge-architecture §3.2 |
| GAP-TAX-01 | taxonomy re-cluster | M | ✓✓ | knowledge-architecture §6 |
| GAP-TAX-02 | binary coverage | M | · | knowledge-architecture §6 |
| GAP-TAX-03 | taxonomy write-only | L | · | knowledge-architecture §6 / roadmap |
| GAP-RETR-01 | gate shadow-mode + stub corpus (activation pending) | S | ✓✓ | knowledge-retrieval-flow |
| GAP-ROUTE-01 | complexity router | L | ✓✓ | platform |
| GAP-ROUTE-02 | medium tier unused | M | ✓ | platform |
| GAP-ROUTE-03 | router LLM fallback | M | · | knowledge-retrieval-flow |
| GAP-TENANCY-01 | is_tenant: new collections done; prod upgrade pending | S | ✓✓ | knowledge-architecture §5 |
| GAP-FED-01 | cross-org federation | XL | — | knowledge-architecture §10.7 / §13 (open question) |
| GAP-MCP-01 | MCP read tools | L | ✓✓ | knowledge-architecture §9 |
| GAP-PRIV-01 | personal enrichment | S | ⊙ | knowledge-architecture §10 |
| ~~GAP-EVAL-01~~ | RAGAS reference — **CLOSED in code 2026-06-11; baseline-v5 pending** | M | ✓✓ | roadmap |
| ~~GAP-EVAL-02~~ | regression canaries — **CLOSED in code 2026-06-11; live canary debug pending** | S | ✓✓ | roadmap |
| ~~GAP-INGEST-02~~ | chunk_type — **CLOSED by removal 2026-06-08** | — | ✓✓ | knowledge-ingest-flow |
