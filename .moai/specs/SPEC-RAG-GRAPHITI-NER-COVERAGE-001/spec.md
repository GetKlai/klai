---
id: SPEC-RAG-GRAPHITI-NER-COVERAGE-001
version: "0.1.0"
status: draft
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
priority: medium
related:
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (upstream — REQ-5 brand-bridging rewrite is the pragmatic mitigation this SPEC complements)
  - SPEC-RAG-EVAL-001 (acceptance gate — uses RAGAS chat suite + brand_bridging canaries)
  - SPEC-RAG-CONTEXTUAL-001 (Tier 1 baseline — extraction operates on `context_prefix + chunk_text`)
  - SPEC-RAG-TAXONOMY-001 (adjacent classifier — Voys still has 0 curated taxonomy nodes)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author       | Change         |
|---------|------------|--------------|----------------|
| 0.1.0   | 2026-05-08 | Mark Vletter | Initial draft  |

---

# SPEC-RAG-GRAPHITI-NER-COVERAGE-001: Graphiti Brand-Name NER Coverage

## Summary

Close the brand-name NER coverage gap in Klai's Graphiti / FalkorDB ingest pipeline. Today, third-party brand names that appear only in list constructions (e.g. *"CRM-systemen zoals Salesforce, HubSpot, Zendesk"*) are not extracted as `Entity` nodes. As a result, graph-search returns zero hits on brand-disambiguation queries, and the retrieval stack is forced to bridge the vocabulary gap exclusively at query-rewrite time (SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-5).

This SPEC adds an opt-in (env-flag, default off) enhanced-extraction path on top of the existing Graphiti `add_episode()` call. It evaluates three concrete techniques on one tenant (Voys) using the existing RAGAS harness and brand-bridging canaries, picks the winner on measurable precision/recall delta vs cost, and ships the chosen technique behind `GRAPHITI_ENHANCED_ENTITY_TYPES=1` for opt-in per-tenant rollout. The rewrite-bridge in the LiteLLM hook stays in place as a fallback for uncovered brands and as a defence-in-depth even after this SPEC ships — they address the same failure mode at different layers (ingest-time vs query-time).

This SPEC is intentionally narrow. It does NOT introduce a parallel local-NER stack (GLiNER / spaCy), bump `graphiti-core` past `0.28.x`, or attempt brand disambiguation. Each of those is reasonable future work; none is justified before single-tenant prove-out of the LLM-prompt route lands.

## Motivation

### Triggering case

Voys-Salesforce conversation, 2026-05-07 19:30 UTC on `chat-voys.getklai.com`. User asked *"Ik wil Voys Freedom graag koppelen aan Salesforce. Hoe werkt dat?"*. The retrieval stack returned `max(reranker_scores_top5) = 0.18` — noise — and the model fabricated *"WhatsApp + Zapier"* as Salesforce integration routes. Neither route exists in any chunk of the Voys knowledge base.

Diagnosis on the live FalkorDB graph confirmed two facts (recorded verbatim in `docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md`):

> *"Cypher-debug confirmed that `Salesforce` is not extracted as an entity in Voys's knowledge graph (only generic `CRM` is). Brand-name-in-list-construction NER coverage gap. Out of scope for this SPEC; query-rewrite (REQ-5) is the pragmatic bridge until ingest-time NER improves."*

> *"BGE-M3 dense + sparse + reranker do not bridge `Salesforce ↔ Bubble`. Confirmed via Cypher on FalkorDB: `Salesforce` is not even an entity in Voys's knowledge graph (only generic `CRM`, `CRM-systeem`, `CRM software` are)."*

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (PRs #516, #517, #518, merged 2026-05-07/08) added a query-rewrite bridge: when a query mentions a brand, the rewriter prepends category synonyms to the embedding query. Live evidence shows reranker top-1 recovered from 0.18 to 0.96 on the same query class. That fix is sufficient for known brands the rewriter knows about, but it cannot bridge brands it has never seen, and it shifts the burden of synonym maintenance into a klai-fast prompt that has to be edited and redeployed for every new third-party product class.

The structural fix is upstream, at ingest-time: extract brand names AS entities so graph-search can complete the bridge `Salesforce → CRM → Bubble` without prompt-engineering at query time.

### Why this is fixable now

`graphiti-core[falkordb]>=0.28,<0.29` (current pin in `klai-knowledge-ingest/pyproject.toml`) exposes three relevant parameters on `add_episode()` that Klai is not using today:

- `entity_types: dict[str, type[BaseModel]]` — Pydantic models that constrain and prime extraction toward specified types
- `excluded_entity_types: list[str]` — filter out unwanted types after extraction
- `custom_extraction_instructions: str` — additional guidance string spliced into both the entity- and edge-extraction prompts

Klai's current call (`klai-knowledge-ingest/knowledge_ingest/graph.py:535-542`) passes none of these. Extraction relies entirely on Graphiti's default prompt + Mistral klai-fast. The default prompt is generic ("extract entities, relationships, dates") and does not mention brand or product names; klai-fast is the smallest model in the stack and may be under-resourced for fine-grained brand disambiguation embedded in narrative prose.

### Why brand-NER, not a parallel stack

GLiNER (DeBERTa-base, ~700MB GPU footprint) reliably beats LLMs on brand-name extraction in milliseconds and is increasingly the industry pattern for production brand-NER. It is, however, a new service-class for Klai: a separate inference container, GPU sharing with TEI / bge-m3 / Infinity, Procrastinate job coordination, and an additional failure mode in the ingest hot path. The LLM-prompt route (this SPEC) reuses every existing component (Graphiti, klai-fast / klai-medium via LiteLLM proxy, FalkorDB) and ships behind an env flag in single-tenant rollout. If this SPEC's chosen technique fails to clear REQ-3's threshold on Voys, the next step is GLiNER as a follow-up SPEC — but only with single-tenant LLM-prompt-route data in hand to justify the operational cost.

### Roadmap alignment

Verified against `docs/architecture/retrieval-improvements-roadmap.md` (read in full per `spec-scope-without-roadmap-check (MED)` pitfall): Tier 1 + Tier 2 are SHIPPED, Tier 3 is gated on 4 weeks of post-launch traces. This SPEC is NOT a Tier 3 item — Tier 3 covers HyDE, GraphRAG community summaries, and Agentic RAG, all retrieval-side. This SPEC is an ingest-side gap-closer that the roadmap implicitly assumed worked (Graphiti is listed as the knowledge-graph layer with no caveat about NER coverage). Closing the assumption-gap is appropriate now, in parallel with the Tier 3 production-trace soak.

## Scope

### In scope

**Diagnostic fase (REQ-1)**

- Cypher exhaustive coverage probe against Voys's live FalkorDB.
- Brand catalogue: 50–100 brand/product names that appear in chunk text but are absent from the entity graph.
- Baseline measurement: per-canary `context_precision` and `context_recall` on the 7 brand-bridging canaries already in `chat.yaml`, captured BEFORE any change ships. Variant tag `pre-ner-coverage-v1`.

**Hypotheses test (REQ-2)**

- Three candidate techniques (A, B, C) implemented behind a single env flag with a sub-flag selecting which technique is active.
- One-tenant rollout (Voys) with eval-harness measurement after each technique's bake-in window.
- Decision record committed to the SPEC ADR section before merging the winner as default.

**Eval gate (REQ-3)**

- Brand-bridging context_recall ≥ 0.50 on the 7 brand_bridging canaries (today: most are << 0.30).
- Aggregate non-brand-bridging chat-suite no regression > 0.02 on any of `context_precision`, `context_recall`, `faithfulness`, `answer_relevance`.

**Cost-budget (REQ-4)**

- Extraction-cost delta ≤ 30% vs current Graphiti-only baseline. 30% is the standing cap from Tier 1+2 (`docs/architecture/retrieval-improvements-roadmap.md` § "No regression on cost").

**Rollout (REQ-5)**

- Env flag `GRAPHITI_ENHANCED_ENTITY_TYPES=0|1` (default `0`).
- No FalkorDB schema changes.
- Tenant without flag shows byte-equivalent extraction behaviour (regression-tested).

**Observability (REQ-6)**

- Prometheus counter `graphiti_entity_extraction_total{entity_type, org_id}`.
- One Grafana panel pinned next to existing `RAG Quality > Low-Confidence`: per-tenant rate of `Brand` / `Product` / `IntegrationPartner` extractions over 24h.

**Pre-flight environment work (REQ-5 sub)**

- `GRAPHITI_ENHANCED_ENTITY_TYPES` declared in `klai-infra/core-01/.env.sops` BEFORE the validator-bearing release lands. Per `validator-env-parity (HIGH)` pitfall.

### Exclusions (What NOT to Build)

The following items are explicitly out-of-scope. Each carries a reason that should NOT be revisited inside this SPEC:

- **Parallel local-NER pipeline (GLiNER / spaCy / DeBERTa-NER).** Reason: introduces a new service-class (GPU footprint, scheduler coordination, additional failure mode in the ingest hot path) before the LLM-prompt route has been measured on a real tenant. If this SPEC's chosen technique fails REQ-3, GLiNER becomes the next SPEC — not this one.
- **`graphiti-core` version bump beyond `>=0.28,<0.29`.** Reason: upstream stability cadence is independent of Klai's RAG roadmap; the entity-types API needed for this SPEC is fully present in 0.28. A 0.29 bump would carry its own breaking-change audit.
- **Entity disambiguation across surface forms.** Reason: making `Salesforce`, `salesforce.com`, `SFDC` resolve to the same `Entity` node is a meaningful feature but a separate problem (alias resolution, equivalence classes, LLM-arbitration). It is also blocked by no-disambiguation-without-coverage: there is nothing to disambiguate today because the entities don't exist.
- **Re-ingest of historical artifacts on tenants other than Voys.** Reason: this SPEC ships per-tenant via env flag. Other tenants opt in via a separate runbook action (re-run `rebuild_kb` after flipping the flag) and are not in scope here.
- **Custom edge types / `edge_types` parameter.** Reason: the failure mode is missing nodes, not missing edges. Adding edge typing without a measured node-coverage gain is premature optimization.
- **Re-tuning Mistral klai-fast vs klai-medium for extraction.** Reason: the model-selection hypothesis is captured as part of technique evaluation in REQ-2; re-tuning the proxy itself is out of scope.
- **Changes to retrieval-time scoring or reranker behaviour.** Reason: this SPEC is ingest-side. Any retrieval-side change would invalidate the measurement framework (REQ-3 baseline must compare apples-to-apples on retrieval-api `main`).

## Requirements (EARS)

### REQ-1: Diagnostic Cypher coverage probe

**Ubiquitous.** The system SHALL execute a documented Cypher coverage probe on Voys's FalkorDB graph and produce a brand catalogue of 50–100 brand/product names that occur in chunk text but are absent from the entity graph, persisted as a CSV artefact at `docs/reports/graphiti-ner-coverage-2026-05-{DD}.md` (date set at probe-run time).

**Sample probe (Cypher run via `falkordb` Python client against the `voys` graph):**

```cypher
// Step 1 — list all distinct entity names extracted today
MATCH (n:Entity {group_id: $org_id})
RETURN DISTINCT n.name AS name, count(*) AS occurrences
ORDER BY occurrences DESC
```

```cypher
// Step 2 — list entity names whose surface is generic (length-based heuristic)
MATCH (n:Entity {group_id: $org_id})
WHERE size(n.name) <= 6 OR n.name IN ['CRM', 'API', 'KvK', 'BTW', 'PIN']
RETURN n.name, count(*) ORDER BY count(*) DESC
```

The brand-catalogue construction step uses the chunk corpus from Qdrant (filter `org_id = "voys"`), runs a regex-based candidate extractor over `context_prefix + chunk_text` for capitalised noun-phrases not already in the Step-1 entity list, and curates the top 50–100 by frequency. The probe script SHALL be checked into `klai-knowledge-ingest/scripts/probe_ner_coverage.py` so it is re-runnable per tenant.

**Acceptance:**

- [ ] Probe script committed and runnable via `docker exec klai-core-knowledge-ingest-1 python scripts/probe_ner_coverage.py --org-id voys`
- [ ] Output CSV with columns `surface_form`, `chunk_occurrences`, `is_entity_today`, `category_guess` (Brand / Product / IntegrationPartner / Other)
- [ ] At least 50 confirmed missing brand/product names in the catalogue (sanity check that the gap is real and not e.g. a Cypher syntax error)
- [ ] Counter-example check: confirm that at least one brand which IS extracted today (e.g. `Pipedrive`, per existing easy-lookup canary) appears in the entity list — proves the probe is working both ways.

### REQ-2: Three candidate techniques evaluated on one tenant

**Event-driven.** WHEN `GRAPHITI_ENHANCED_ENTITY_TYPES=1` AND `GRAPHITI_NER_TECHNIQUE` is set to one of `entity_types`, `extraction_instructions`, `pre_pass_hints`, the system SHALL apply the corresponding technique inside `ingest_episode()` (`klai-knowledge-ingest/knowledge_ingest/graph.py:497`) before delegating to `graphiti.add_episode()`.

**Technique A — `entity_types`:** define Pydantic models `Brand`, `Product`, `IntegrationPartner` and pass them via `add_episode(..., entity_types={...})`. Models include a docstring with concrete examples for the LLM to anchor on (per Graphiti 0.28 conventions).

**Technique B — `custom_extraction_instructions`:** pass a short Dutch + English instruction string via `add_episode(..., custom_extraction_instructions=...)` that explicitly enumerates the categories of interest ("brand names of third-party software products, integration partners, telephony hardware brands, CRM systems, video-conference platforms"). No Pydantic models.

**Technique C — pre-pass hints:** before `add_episode()`, run a single-shot klai-fast call over `context_prefix + chunk_text` that returns a list of capitalised proper-nouns / product-names found in the text, then splice that list into `custom_extraction_instructions` as concrete entity hints for that specific episode. Reuses the same LiteLLM proxy and rate-limit infrastructure (`_RateLimitedTransport`).

**Acceptance:**

- [ ] All three techniques implemented in a single PR, each behind `GRAPHITI_NER_TECHNIQUE=<name>` selector
- [ ] Pydantic config validators reject unknown technique names at startup (per `klai-portal/backend/app/middleware/klai_cors.py` validator pattern)
- [ ] Each technique evaluated against the brand-bridging canaries via `docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.eval --suite chat --variant ner-coverage-{A|B|C}-v1`, with results stored in `knowledge.rag_eval_results`
- [ ] ADR section appended to this SPEC (or sibling `decision-record.md`) with the per-technique result table and the chosen technique. Format: `technique | context_recall_brand_bridging | context_precision_brand_bridging | extraction_cost_delta_pct | extraction_latency_p95_ms | decision`
- [ ] The chosen technique becomes the implicit default when `GRAPHITI_ENHANCED_ENTITY_TYPES=1` and `GRAPHITI_NER_TECHNIQUE` is unset

### REQ-3: Eval gate — brand-bridging recall ≥ 0.50, no aggregate regression

**State-driven.** WHILE `GRAPHITI_ENHANCED_ENTITY_TYPES=1` is active on Voys AND the chosen technique from REQ-2 has been re-ingested across the corpus via `rebuild_kb`, the system SHALL satisfy the following acceptance criteria measured by `knowledge_ingest.eval` against the existing `chat.yaml` suite (37 queries, including the 7 brand_bridging canaries from SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-7):

- Brand-bridging cohort (`mix: brand_bridging`): mean `context_recall ≥ 0.50` (today: most are `< 0.30`)
- Negative-class canary `chat-brand-not-in-kb`: still answers "Dat staat niet in de kennisbank" (anti-hallucination injection still fires)
- Aggregate non-brand-bridging cohort (`mix != brand_bridging`): no regression `> 0.02` on any of `context_precision`, `context_recall`, `faithfulness`, `answer_relevance` vs the `pre-ner-coverage-v1` baseline captured in REQ-1

**Acceptance:**

- [ ] Variant `pre-ner-coverage-v1` captured before any code change ships (immutable baseline)
- [ ] Variant `ner-coverage-{chosen}-v1` captured after rebuild_kb completes on Voys
- [ ] Pairwise delta computed and pasted into the SPEC's HISTORY appendix (or sibling `eval-results.md`)
- [ ] If aggregate regression `> 0.02` appears on any non-brand-bridging metric, the technique is rejected and REQ-2 re-opens — SPEC does NOT ship in that case

### REQ-4: Cost-budget — ≤ 30% extraction cost increase

**Ubiquitous.** The system SHALL keep the extraction-cost delta (Mistral klai-fast tokens per artifact, measured over the rebuild_kb run) ≤ 30% vs the current Graphiti-only baseline.

**Measurement basis:** retrieval-improvements-roadmap.md § "No regression on cost" pins the standing 30% cap on combined Tier 1+2 cost. This SPEC's budget is a separate 30% headroom on top of today's per-episode `add_episode()` token cost specifically — NOT a cumulative 30% across all retrieval improvements. Distinct because Tier 1+2 cost is at query-time (rewrite + classify); this SPEC's cost is at ingest-time (per-episode extraction). Both are below user-facing latency. Concretely:

- Baseline: total Mistral input + output tokens consumed across all `add_episode()` calls during the most recent full Voys rebuild_kb run, captured from VictoriaLogs `service:knowledge-ingest AND event:graphiti_episode_ingested`.
- Post-change: same measurement after rebuild_kb under the chosen technique.
- Delta = `(post / baseline) - 1`. Acceptance threshold: `≤ 0.30`.

**Acceptance:**

- [ ] Pre-change baseline captured in artefact (VictoriaLogs query + raw token sum)
- [ ] Post-change measurement captured in artefact
- [ ] Delta computed and ≤ 0.30 — otherwise the chosen technique is rejected and REQ-2 re-opens
- [ ] Pre-pass hints (Technique C) explicitly accounted for: the additional klai-fast call per chunk is counted in the post-change total

### REQ-5: Backwards-compatibility via env-flag opt-in

**State-driven.** WHILE `GRAPHITI_ENHANCED_ENTITY_TYPES` is unset OR `0`, the system SHALL behave byte-equivalently to the pre-SPEC code path: `add_episode()` is called with the same parameters as today, no new model imports, no new prompt strings, no FalkorDB schema changes.

**Acceptance:**

- [ ] Regression test: with `GRAPHITI_ENHANCED_ENTITY_TYPES=0`, ingest a fixture artifact and assert the `add_episode()` call signature matches the current call site exactly (`name`, `episode_body`, `source`, `source_description`, `reference_time`, `group_id` only)
- [ ] No FalkorDB schema migration. Entity nodes created under any technique still use the standard Graphiti `Entity` label; the `Brand` / `Product` / `IntegrationPartner` types are stored as `entity_type` properties on the Entity node, not as new node labels (verified via `MATCH (n) RETURN DISTINCT labels(n)` after a test ingest)
- [ ] `klai-infra/core-01/.env.sops` updated to declare `GRAPHITI_ENHANCED_ENTITY_TYPES=0` and `GRAPHITI_NER_TECHNIQUE=` (empty) BEFORE the validator-bearing release lands. Per `validator-env-parity (HIGH)` pitfall — env-var-first, validator-second.
- [ ] SOPS roundtrip line-count check passes per `sops-roundtrip-line-count-check (HIGH)` pitfall (delta = +2 vs current). The PR description includes the typed-confirmation invocation `gh workflow run sync-env.yml -f allow_removal=I-CONFIRM-REMOVAL` is NOT used (this is an additive change, not a removal).
- [ ] Pydantic field validator on `Settings.graphiti_enhanced_entity_types` accepts `"0"`, `"1"`, `True`, `False`. Pydantic field validator on `Settings.graphiti_ner_technique` accepts `""`, `"entity_types"`, `"extraction_instructions"`, `"pre_pass_hints"` and rejects anything else with a clear error message.

### REQ-6: Observability — Prometheus counter per entity-type per tenant

**Event-driven.** WHEN `add_episode()` returns an `AddEpisodeResults` containing extracted entities AND the chosen technique is active, the system SHALL increment `graphiti_entity_extraction_total{entity_type=<type>, org_id=<org>}` for each extracted entity, AND emit a structured event `graphiti_entity_extraction_breakdown` at `info` level with the per-type count for the episode.

**Counter rationale:** operators must be able to measure per-tenant extraction coverage WITHOUT opening Cypher. A single PromQL query (`sum by (entity_type, org_id) (rate(graphiti_entity_extraction_total[24h]))`) is sufficient to detect a drop in `Brand` extractions on a per-tenant basis — early warning for prompt drift or upstream model regression.

**Grafana panel:** add a new panel "Graphiti NER extraction by type" to the existing `rag-quality.json` dashboard, sibling to the `Low-Confidence` panel from SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001. Time-series, stacked by `entity_type`, templated on the existing `$tenant` variable. UID per the 40-char pitfall: `spec-ner-001-extraction-by-type` (29 chars, well within limit).

**Acceptance:**

- [ ] Counter declared in `klai-knowledge-ingest/knowledge_ingest/metrics.py` and registered with the existing Prometheus registry
- [ ] Counter increments only when the enhanced path is active (no-op under `GRAPHITI_ENHANCED_ENTITY_TYPES=0`)
- [ ] Grafana panel JSON committed to `deploy/grafana/provisioning/dashboards/rag-quality.json` and provisioning sync verified per `bind-mount-without-sync-workflow (HIGH)` pattern
- [ ] Panel verified live on Voys post-deploy: at least one non-zero data point on `entity_type="Brand"` within 24h of rebuild_kb completing

### REQ-7: Out-of-scope items remain rejected

**Unwanted.** The system SHALL NOT introduce a parallel NER pipeline (GLiNER / spaCy / standalone NER service), bump `graphiti-core` past `0.29`, attempt entity disambiguation, ship custom `edge_types`, change retrieval-time scoring, or trigger automatic re-ingest on tenants other than Voys, within the scope of this SPEC.

**Acceptance:**

- [ ] PR description for the implementation MUST include a checklist confirming none of the rejected items appear in the diff
- [ ] If during implementation a contributor argues an out-of-scope item is "obviously needed", the conversation is paused and a new SPEC is opened — this SPEC stays narrow per `spec-discipline (CRIT)` and `minimal-changes (HIGH)` pitfalls

## Technical approach

### File-level surface (planned)

- `klai-knowledge-ingest/knowledge_ingest/graph.py` — add `_apply_enhanced_extraction()` helper before the `graphiti.add_episode()` call at line 535. Helper dispatches on `settings.graphiti_ner_technique`; returns the kwargs to splice into `add_episode()`. Default returns `{}` (no-op).
- `klai-knowledge-ingest/knowledge_ingest/ner_models.py` (NEW) — Pydantic models `Brand`, `Product`, `IntegrationPartner` for Technique A. Each model has a class-level docstring with concrete examples; Graphiti's prompt builder reads docstrings.
- `klai-knowledge-ingest/knowledge_ingest/ner_instructions.py` (NEW) — string constant `BRAND_EXTRACTION_INSTRUCTIONS_NL_EN` for Technique B.
- `klai-knowledge-ingest/knowledge_ingest/ner_prepass.py` (NEW) — async function `extract_brand_hints_from_text(text, llm_client)` for Technique C. Reuses the existing LiteLLM proxy via the same `OpenAIGenericClient` wired in `_get_graphiti()`.
- `klai-knowledge-ingest/knowledge_ingest/config.py` — add 2 settings fields with validators (`graphiti_enhanced_entity_types: bool`, `graphiti_ner_technique: Literal["", "entity_types", "extraction_instructions", "pre_pass_hints"]`).
- `klai-knowledge-ingest/knowledge_ingest/metrics.py` — declare `graphiti_entity_extraction_total` Prometheus counter.
- `klai-knowledge-ingest/scripts/probe_ner_coverage.py` (NEW) — REQ-1 probe script.
- `deploy/grafana/provisioning/dashboards/rag-quality.json` — add panel.
- `klai-infra/core-01/.env.sops` — add 2 env vars (env-first, per `validator-env-parity (HIGH)`).

### Why no `_patch_graphiti.py` change

Inspected `klai-knowledge-ingest/knowledge_ingest/_patch_graphiti.py`: the existing monkey-patches address (1) FalkorDB edge-search query shape, (2) case-insensitive node dedup, (3) FalkorDriver.clone race condition, (4) decorator group-id routing, (5) bidirectional edge dedup, (6) empty fulltext query guard. None of these touch the entity-extraction prompt path or the `add_episode()` parameter surface. This SPEC adds via parameter passing, not via patching — so no patch-file change.

### Validator-env-parity pre-flight

Per the `validator-env-parity (HIGH)` pitfall, the env vars MUST land in `klai-infra/core-01/.env.sops` BEFORE the Pydantic validator that consumes them ships. Sequence:

1. PR-1 (klai-infra): add `GRAPHITI_ENHANCED_ENTITY_TYPES=0` and `GRAPHITI_NER_TECHNIQUE=` to `core-01/.env.sops`. Verify SOPS roundtrip line-count delta = +2.
2. PR-2 (klai-knowledge-ingest): land the validator + the three techniques behind the env flag.

PR-1 must merge first. CI-side audit per `klai-infra/.github/workflows/sync-env.yml` ensures the `keys-would-be-REMOVED` guard does not fire (this is an additive change).

### Cypher / Python probe outline (REQ-1, persisted as `scripts/probe_ner_coverage.py`)

```python
# Pseudocode; full implementation in REQ-1 deliverable
async def probe_ner_coverage(org_id: str) -> list[dict]:
    # 1. Pull current Entity surface from FalkorDB
    falkor_entities = set(await fetch_entities(org_id))
    # 2. Pull all chunk texts from Qdrant for org_id
    chunks = await qdrant_store.scroll_org(org_id)
    # 3. Regex-extract capitalised noun phrases from context_prefix + chunk_text
    candidates = extract_capitalised_phrases([c["context_prefix"] + "\n" + c["chunk_text"] for c in chunks])
    # 4. Filter: candidate not already an entity, occurs >= 3 times across chunks
    missing = [c for c in candidates if c.surface not in falkor_entities and c.count >= 3]
    # 5. Sort by count desc, take top 100, write CSV
    write_csv(missing[:100], f"docs/reports/graphiti-ner-coverage-{org_id}-{date}.md")
```

### Pre-pass hint (Technique C) cost model

Per chunk, Technique C adds one klai-fast call (input = `context_prefix + chunk_text`, output = JSON list of brand candidates). Existing `add_episode()` already invokes ~5 LLM calls internally per episode (see `graph.py` line 40 comment). One additional small call adds ~20% to per-episode cost in the worst case — well within the REQ-4 30% budget, but the measurement in REQ-4 captures the actual delta and decides.

### Rebuild_kb interaction

Once the chosen technique ships, Voys's existing corpus must be re-ingested for the entity coverage to backfill. This is a runbook action, not new SPEC work: the existing `rebuild_kb` operator script (per `docs/architecture/retrieval-improvements-roadmap.md` § "rebuild_kb operator backfill") re-runs the ingest pipeline against Qdrant snapshots without re-fetching from source. The runbook update is part of REQ-3's measurement step, not a separate SPEC.

## Risks

- **Brand-name false positives.** A technique that prompts for brands aggressively may extract non-brand capitalised tokens (Dutch place names, person names from team-pages). Mitigation: REQ-3's negative canary `chat-brand-not-in-kb` must still produce abstention; if false-positive `Brand` entities pollute the graph and cause hallucination on canary, the technique is rejected.
- **Cost overshoot on Technique C.** Pre-pass hints multiply per-chunk LLM calls. If REQ-4 measurement exceeds 30%, Technique C is rejected even if its recall is best — the SPEC author is not allowed to special-case the budget.
- **Tenant variance.** Voys is a phone/CRM-heavy tenant. A technique that wins on Voys may not generalise to a tenant whose KB is medical, legal, or e-commerce. Mitigation: SPEC ships as opt-in per tenant; per-tenant evaluation is a runbook step before flipping the flag for a new tenant. This is a known limitation of the single-tenant prove-out approach and is accepted.
- **Graphiti upstream prompt change.** A future `graphiti-core` patch may alter the default extraction prompt's relationship with `entity_types` / `custom_extraction_instructions`. Mitigation: REQ-7 freezes the version at `>=0.28,<0.29`; any 0.29 bump goes through its own audit. Existing `_patch_graphiti.py` already pins us to 0.28.x behaviour.
- **Env-flag rollback path.** If post-deploy Voys traces show regression, operator flips `GRAPHITI_ENHANCED_ENTITY_TYPES=0` (no SOPS edit needed if env-var update is a `docker compose up -d` away). New entities already extracted under the enhanced path remain in the graph; they do not need to be cleaned up because they are valid extractions, just at higher coverage. No FalkorDB cleanup required on rollback.

## Decision Record

(To be appended after REQ-2 evaluations complete. Empty at draft time.)

| Technique | brand_bridging context_recall | brand_bridging context_precision | extraction_cost_delta | extraction_latency_p95_ms | decision |
|-----------|---------|---------|---------|---------|-----------|
| A — entity_types | TBD | TBD | TBD | TBD | TBD |
| B — extraction_instructions | TBD | TBD | TBD | TBD | TBD |
| C — pre_pass_hints | TBD | TBD | TBD | TBD | TBD |

Chosen technique: **TBD**
Rationale: **TBD**
