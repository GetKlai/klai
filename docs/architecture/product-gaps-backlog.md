# Product Gaps Backlog — where the docs describe more than the code does

> Created 2026-06-08 from a doc-vs-code drift audit of `docs/architecture/`.
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

## Not a gap — a real bug (tracked separately)

The temporal "currently-believed" retrieval filter is dead: ingest writes
`valid_from` / `valid_until` to the Qdrant payload, but retrieval filters on a
`invalid_at` field that ingest never writes, and never reads `valid_until`. See
[`docs/retros/2026-06-08-temporal-filter-field-mismatch.md`](../retros/2026-06-08-temporal-filter-field-mismatch.md).
Listed here for completeness as `GAP-TEMPORAL-01` but it should be fixed as a
bug, not designed as a feature.

---

## Priority view

### Quick wins (S effort, high value — close to config changes)

| Gap | One-liner | Verif |
|---|---|---|
| `GAP-RETR-01` | Retrieval gate never bypasses (reference corpus never shipped) → every trivial query runs the full pipeline | ✓ |
| `GAP-TENANCY-01` | Qdrant `org_id` index missing `is_tenant` flag → no per-tenant HNSW subgraph / recall guarantee | ✓✓ |
| `GAP-EVID-01` | Assertion-mode scoring dimension multiplies by a constant `1.00` | ✓ |
| `GAP-INGEST-02` | `chunk_type` LLM classification is stored but never read by any retrieval consumer | ✓ |
| `GAP-MCP-01` | MCP `search_knowledge` hardcodes `scope: "both"` (no org-only / personal-only / type / time filters) | ✓✓ |
| `GAP-TEMPORAL-01` | Temporal filter targets a non-existent field — **bug**, see retro | ✓✓ |

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

### GAP-PROV-01 — Provenance DAG / entity registry / supersession is schema-only  ·  L  ·  ⊙
- **Intended (§3.3, §5):** a `derivations` adjacency DAG, a `knowledge.entities`
  registry + `artifact_entities`, and a `superseded_by` chain — powering an
  invalidation cascade ("retract src-2847 → flag everything derived from it")
  and confidence-from-source-count, plus `WITH RECURSIVE` lineage analytics.
- **Reality:** all four structures exist in `0001_baseline.py` but production
  code never populates them: **0 INSERTs** into `derivations` / `entities` /
  `artifact_entities`; `superseded_by` is only ever set to `NULL`. Entity data
  that *is* produced (Graphiti) lives in FalkorDB + the Qdrant payload, not the
  PG registry the doc's SQL queries.
- **Why it matters:** the cascade and confidence calibration the doc presents as
  *enabled* have no inputs to run on; the analytical queries return nothing.
- **Evidence:** `klai-knowledge-ingest/alembic/versions/0001_baseline.py:115,144-150,213-219,236-250`;
  `pg_store.py:305,433,524` (only `superseded_by = NULL`); 0 INSERTs (verified by hand).

### GAP-PROV-02 — `derived_from` empty, `confidence` a manual label  ·  L  ·  ·
- **Intended (§3.3):** every artifact keeps a `derived_from` chain (source+span)
  and a `confidence` derived from independent-source count (example: 0.87 from 3
  sources).
- **Reality:** MCP write tools store `derived_from = []` and put attribution in a
  human-readable `source_note` string; `confidence` is a `high/medium/low`
  frontmatter enum, never computed and never read in retrieval scoring (0 hits
  in `retrieval_api/services`).
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

### GAP-EVID-01 — Assertion-mode weight is a constant 1.00  ·  S  ·  ✓
- **Intended (§7.4):** assertion-mode is one of four multiplicative scoring
  dimensions (`final = reranker × content_type × assertion_mode × temporal ×
  pagerank`) with its own default-true flag.
- **Reality:** `_assertion_weight()` ignores its input and the flag and returns
  `1.00` ("plumbing only in v1"); `assertion_mode_weights = {}`. The field is
  classified, stored, and threaded to the scorer purely to be multiplied by 1.0.
- **Note:** §7.4 is honest that weights are flat pending SPEC-EVIDENCE-002, but
  §3.2 reads as present-tense scoring. A ready-to-activate lever, not a silent
  absence.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/evidence_tier.py:51,113-116,167`.

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
- **Reality:** `run_taxonomy_clustering` is registered but has **no `@periodic`
  decorator** and no caller; only the manual admin "bootstrap" endpoint
  populates a taxonomy. A KB's taxonomy is frozen at bootstrap; new docs are
  classified against the frozen set but never trigger new-node proposals. No
  outlier/velocity monitoring, no merge surfacing.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/clustering_tasks.py:36,44`
  (no `@periodic`, contrast `ragas_runner.py:230`, `stale_pending_artifact_reaper.py:52`); verified by hand.

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

### GAP-RETR-01 — Retrieval gate never bypasses (dead in prod)  ·  S  ·  ✓
- **Intended (knowledge-retrieval-flow §Gate):** a semantic gate that skips KB
  lookup for general-knowledge queries (`RETRIEVAL_GATE_ENABLED=true`).
- **Reality:** `gate.should_bypass()` loads `data/gate_reference.jsonl`, which
  does not exist (`data/` has only `.gitkeep`) and is never generated at
  build/deploy. So `should_bypass()` returns `(False, None)` unconditionally —
  every query, trivial or not, runs the full embed + 3/4-leg + rerank pipeline.
  A generator script exists but is manual-only.
- **Why it matters:** latency + embedding/GPU cost on every trivial message, and
  off-topic KB chunks can still be injected — the exact failure mode the gate was
  built to prevent. Closing it = commit a curated `gate_reference.jsonl` or run
  the generator at deploy.
- **Evidence:** `klai-retrieval-api/retrieval_api/services/gate.py:33-45,91-104`;
  `scripts/generate_gate_reference.py` (manual-only); `data/.gitkeep` only.

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

### GAP-TENANCY-01 — Qdrant `is_tenant` flag not set  ·  S  ·  ✓✓
- **Intended (§5.1):** `org_id` payload index with `is_tenant: true` (Qdrant 1.12+
  native multitenancy → per-tenant HNSW subgraph, no recall degradation) plus
  Qdrant 1.16 tiered sharding for large tenants.
- **Reality:** `org_id` is a plain `keyword` index; **0** `is_tenant` /
  `shard_key` references. Isolation works via a mandatory `org_id` must-filter at
  query time — i.e. the exact unindexed-for-tenancy config the doc says degrades
  recall.
- **Why it matters:** correctness is fine, but the recall/scale guarantee the doc
  cites as the reason single-collection multitenancy is acceptable is not in
  place. `is_tenant=True` is one `create_payload_index` parameter.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:82-105,473`;
  `klai-retrieval-api/retrieval_api/services/search.py:76-77`; verified by hand.

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

### GAP-EVAL-01 — RAGAS ground-truth is a topic-label string  ·  M  ·  ✓
- **Intended (roadmap):** headline `context_precision +61%` / `context_recall
  +154%` measured by a RAGAS harness over 60 hand-curated Voys queries.
- **Reality:** the RAGAS `reference` is `', '.join(expected_topics)` — a 2-3
  keyword label (e.g. "bubble, troubleshoot"), not a reference answer. RAGAS
  context metrics are designed to score against a full reference answer, so the
  headline deltas partly measure keyword overlap, not answerability.
- **Why it matters:** these metrics gate the Tier-3 decision; a weak ground-truth
  risks wrong prioritisation (HyDE vs GraphRAG vs Agentic).
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py:275,297-310`;
  `eval/suites/chat.yaml:17,25,33`.

### GAP-EVAL-02 — Regression canaries never checked  ·  S  ·  ·
- **Intended (roadmap):** 9 `easy_lookup` "regression canaries" with
  `expected_chunks` populated.
- **Reality:** `expected_chunks` is loaded into `SuiteQuery` but the runner never
  compares retrieved vs expected chunks — no canary hit/miss metric. A regression
  that drops a canary's known-good chunk is invisible as long as the fuzzy RAGAS
  score stays plausible.
- **Evidence:** `eval/ragas_runner.py:108-144`; `eval/judge_client.py:227-338`;
  `tests/eval/test_seed_suites.py:71-89`.

### GAP-INGEST-02 — `chunk_type` classified but never consumed  ·  M  ·  ✓
- **Intended (ingest-flow):** per-chunk `chunk_type`
  (procedural|conceptual|reference|warning|example) "for downstream consumption by
  retrieval routers".
- **Reality:** ingest pays an LLM classification (+ a retry round-trip on invalid
  values) and stores it to the Qdrant payload, but **no** retrieval consumer reads
  `chunk_type` — not the router, diversity, reranker, evidence-tier, or any
  filter. (Distinct from the *document*-level `content_type`, which evidence-tier
  does use.)
- **Why it matters:** either wire it into retrieval (prefer `procedural` chunks
  for "how do I…", surface `warning` chunks for risk questions) or stop paying for
  the label.
- **Evidence:** `klai-knowledge-ingest/knowledge_ingest/enrichment.py:133-135,368-402`;
  `qdrant_store.py:330-331`; 0 `chunk_type` consumers in `retrieval_api`.

---

## Appendix — gap index

| ID | Area | Effort | Verif | Doc |
|---|---|---|---|---|
| GAP-TEMPORAL-01 | temporal filter (bug) | S | ✓✓ | knowledge-architecture §5 / retrieval-flow |
| GAP-LOOP-01 | gap LLM-judge | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-02 | semantic gap registry | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-03 | gap lifecycle | L | ✓ | knowledge-architecture §8 |
| GAP-LOOP-04 | editorial priority | M | ✓ | knowledge-architecture §8 |
| GAP-LOOP-05 | gap trigger signals | L | ✓ | knowledge-architecture §8,§12 |
| GAP-LOOP-06 | AI-draft | L | · | knowledge-architecture §12 |
| GAP-PROV-01 | provenance DAG/entities | L | ⊙ | knowledge-architecture §3,§5 |
| GAP-PROV-02 | derived_from/confidence | L | · | knowledge-architecture §3 |
| GAP-SYNC-01 | dual-store outbox | L | ⊙ | knowledge-architecture §5 |
| GAP-EVID-01 | assertion-mode weight | S | ✓ | knowledge-architecture §7.4 |
| GAP-EVID-02 | 3-into-5 grouping | M | ✓ | knowledge-architecture §3.2 |
| GAP-TAX-01 | taxonomy re-cluster | M | ✓✓ | knowledge-architecture §6 |
| GAP-TAX-02 | binary coverage | M | · | knowledge-architecture §6 |
| GAP-TAX-03 | taxonomy write-only | L | · | knowledge-architecture §6 / roadmap |
| GAP-RETR-01 | retrieval gate inert | S | ✓ | knowledge-retrieval-flow |
| GAP-ROUTE-01 | complexity router | L | ✓✓ | platform |
| GAP-ROUTE-02 | medium tier unused | M | ✓ | platform |
| GAP-ROUTE-03 | router LLM fallback | M | · | knowledge-retrieval-flow |
| GAP-TENANCY-01 | is_tenant flag | S | ✓✓ | knowledge-architecture §5 |
| GAP-FED-01 | cross-org federation | XL | — | knowledge-architecture §10.7 / §13 (open question) |
| GAP-MCP-01 | MCP read tools | L | ✓✓ | knowledge-architecture §9 |
| GAP-PRIV-01 | personal enrichment | S | ⊙ | knowledge-architecture §10 |
| GAP-EVAL-01 | RAGAS reference | M | ✓ | roadmap |
| GAP-EVAL-02 | regression canaries | S | · | roadmap |
| GAP-INGEST-02 | chunk_type unused | M | ✓ | knowledge-ingest-flow |
