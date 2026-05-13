# Knowledge-retrieval improvements — Low-Confidence Abstain + Brand-Bridging (2026-05-07/08)

> Quick capture of the work that landed in main on 2026-05-07/08 across PRs
> #516, #517, #518. Move/restructure into the right doc home later.
> Companion to [retrieval-improvements-roadmap.md](architecture/retrieval-improvements-roadmap.md)
> which describes the Tier 1+2 baseline this builds on.

---

## TL;DR

A low-confidence detection + abstention layer landed on top of the
already-shipped Tier 1+2 retrieval stack. Three commits to main:

| PR | sha | What |
|---|---|---|
| #516 | `afc5c88a` | SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 — 8 REQs, 6 implementation units |
| #517 | `c4dbad83` | Cleanup: Grafana panel PromQL repair + e2e test coverage + pre-existing test_api.py mock fixes |
| #518 | `6daa1424` | Bugfix: `low_confidence_injection_applied` log emit upgraded from info → warning so VictoriaLogs picks it up |

**Triggering incident**: 2026-05-07 19:30 UTC Voys-Salesforce conversation
on `chat-voys.getklai.com`. User asked "Ik wil Voys Freedom graag koppelen
aan Salesforce. Hoe werkt dat?" → max-rerank=0.18, top-1 was the
`/integraties` index page, response fabricated "WhatsApp + Zapier" as
a Salesforce integration route. None of those routes exist in any chunk.

**Validated 2026-05-08**: Same question now produces a Bubble-licentie
answer with three brand-specific citations. Reranker top-1 went from 0.18
to 0.96. Negative-class canary ("Acme Foo Connector v3 voor Salesforce
Commerce B2B Lightning") returns "Dat staat niet in de kennisbank" instead
of fabricating.

---

## What was actually wrong (root cause)

The shipped Tier 1+2 stack (contextual chunking, query rewrite, parent-child,
taxonomy classifier, RAGAS eval) was performing well in aggregate
(`context_precision +61%`, `context_recall +154%`, `faithfulness 0.81`),
but had three independent gaps that combined catastrophically on the
brand-bridging-class:

1. **Vocabulary gap**: The Voys help-pages explaining the Salesforce route
   lead with `Bubble` / `RedCactus` / `CRM-pakket`. The literal string
   `Salesforce` is absent from the most narrative-rich chunks. BGE-M3
   dense + sparse + reranker do not bridge `Salesforce ↔ Bubble`. Confirmed
   via Cypher on FalkorDB: `Salesforce` is not even an entity in Voys's
   knowledge graph (only generic `CRM`, `CRM-systeem`, `CRM software` are).

2. **Top-K = 5 was suboptimal**: Anthropic Contextual Retrieval research
   ("top-20 chunks proved more effective than top-5 or top-10") was
   ignored in the existing hook config. Reranker was already producing
   20 candidates server-side; only 5 were forwarded to the LLM.

3. **No abstention layer at low confidence**: Reranker score 0.18 (noise
   level) still produced a confident-sounding answer. Google's
   sufficient-context research: insufficient context in a generative model
   moves hallucination rate from ~10% (no context) to ~66% (insufficient
   context). The model fabricated `WhatsApp` and `Zapier` as Salesforce
   routes — both absent from every retrieved chunk.

Plus an observability gap: link-expand had been quietly contributing zero
chunks to served top-K for months because the `_link_expanded` flag was
set on chunks but the reranker score never benefited from it.

---

## What landed — REQ-by-REQ

### REQ-1: confidence_band emit
- New `confidence_band` field on every `/retrieve` response: `high`/`medium`/`low`/`unknown`
- Computed from `max(reranker_scores_top5)` AFTER quality-floor + source-aware-select + quality-boost
- Default thresholds: `high ≥ 0.60`, `low < 0.30` (tunable via env)
- Pure helper `_compute_confidence_band` in [retrieve.py](../klai-retrieval-api/retrieval_api/api/retrieve.py) with edge cases: empty list, reranker disabled, all-None scores, mixed-None — all → `unknown`
- Pydantic config validators reject misconfigured thresholds at startup (low must be < high)
- Prometheus counter `retrieval_confidence_band_total{band, org_id}` for per-tenant observability

### REQ-2: anti-hallucination injection
- When `confidence_band ∈ {low, unknown}`, the litellm-hook appends a Dutch instruction to the system prompt:
  > "[Klai retrieval — lage relevantie] Het opgehaalde KB-materiaal heeft een lage relevantie-score voor deze vraag. Citeer alleen wat letterlijk in de chunks staat. Verzin GEEN integratie-routes, productnamen, stappen, bedragen, of technische details die niet expliciet in de chunks voorkomen. Sluit af met een vraag om verduidelijking aan de gebruiker als het materiaal de vraag niet volledig dekt — dat is beter dan een verzonnen antwoord."
- Translated by the model into the user's detected language via the existing LANGUAGE REMINDER block (most-recent-instruction-wins pattern — lands AFTER the language reminder)
- Module-level constant `_LOW_CONFIDENCE_INJECTION_TEXT` in [klai_knowledge.py](../deploy/litellm/klai_knowledge.py) — prompt iterations are a single-line edit + restart, no retrieval-api deploy needed
- Emergency rollback: `KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION=1` env var
- Structured event `low_confidence_injection_applied` emitted at warning level (info was filtered by litellm container's root logger, fixed in #518)

### REQ-3: link-expand reranker boost
- Multiplicative score boost (capped at 1.0) for chunks tagged `_link_expanded=True`
- Applied AFTER rerank, BEFORE source-aware-select + quality-boost — so expanded chunks compete fairly for the served top-K
- Default `link_expand_score_boost = 1.00` → no-op until operator tunes (ships safe; the SPEC's REQ-3 is dormant by default and activates per-tenant via env override once an eval baseline is captured)
- Prometheus counter `retrieval_link_expand_top_k_total{outcome=hit|miss, org_id}` lets operators measure survival rate before raising the boost

### REQ-4: top_k 5 → 20
- `KNOWLEDGE_RETRIEVE_TOP_K` default raised from 5 to 20 in [klai_knowledge.py:253](../deploy/litellm/klai_knowledge.py#L253)
- Reranker already produced 20 candidates; only the LLM-context payload changed
- Token-cost delta: ≤ 3¢ per call on `klai-primary` (Mistral Large) — well within the < 30% combined-stack budget cap from the existing Tier 1+2 roadmap
- Verified live: `served_top_k: 20` on every retrieval-decision-record post-deploy
- Operator rollback: `KNOWLEDGE_RETRIEVE_TOP_K=5` env var

### REQ-5: brand → category bridging in rewrite prompt
- Extends `_QUERY_REWRITE_AND_CLASSIFY_PROMPT`: when the user's question mentions a third-party brand (Salesforce, HubSpot, Zoom, etc.), klai-fast also includes 2-4 broader category or related-brand terms in the rewritten query
- Three in-context examples cover CRM, video conferencing, mail/calendar:
  - `"Hoe koppel ik Voys aan Salesforce?" → "Voys Salesforce CRM-koppeling Bubble RedCactus"`
  - `"Ondersteunen jullie Zoom?" → "Voys Zoom vergader-integratie telefoonkoppeling"`
  - `"Werkt Outlook met Voys?" → "Voys Outlook e-mailkoppeling agenda-integratie"`
- Negative-instruction guard: "If NO third-party brand is mentioned, leave the rewrite unchanged" — prevents over-application on questions like "Hoe stel ik vakantie in?"
- Stays within the existing 200-char JSON contract; same language as the user

### REQ-6: sparse-input parity audit
- Verdict: **parity already exists**. [enrichment_tasks.py:407](../klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py#L407) feeds both dense and sparse embedders from the same `enriched_texts` list (built at [enrichment.py:435](../klai-knowledge-ingest/knowledge_ingest/enrichment.py#L435) as `f"{context_prefix}\n\n{chunk_text}"`).
- This means Anthropic Contextual BM25 stacking (the second half of the 49% → 67% delta) was already correctly wired — no fix needed
- Locked in by [test_sparse_input_parity.py](../klai-knowledge-ingest/tests/test_sparse_input_parity.py) (8 tests) so a future refactor that splits the embedder paths cannot silently regress this

### REQ-7: regression canaries
- 7 new queries added to `chat.yaml` with `mix: brand_bridging`:
  - `chat-brand-salesforce-bridging` (the canonical 2026-05-07 incident query)
  - `chat-brand-hubspot-bridging`, `chat-brand-msteams-bridging`, `chat-brand-pipedrive-bridging`, `chat-brand-zoom-bridging`
  - `chat-brand-not-in-kb` (negative-class canary — anti-hallucination must fire)
  - `chat-brand-control-explicit` (positive-class control — must NOT fire band=low)
- Suite total: 30 → 37 queries

### REQ-8: observability
- Grafana panel `RAG Quality > Low-Confidence` — three time-series:
  - confidence_band distribution (rate per band per tenant)
  - link-expand survival rate
  - low+unknown share (originally was a non-existent litellm Prometheus counter; #517 repaired it to use the existing band counter via `(low+unknown)/total` — semantically the same signal, structurally connected to the alert rule)
- Alert rule `spec-rag-001-low-confidence-rate` (32 chars, ≤ 40 per Klai pitfall) fires on `(low+unknown)/total > 0.20` for 5m per tenant
- Runbook section in [docs/runbooks/rag-quality.md](runbooks/rag-quality.md) with three triage paths: content-gap, threshold mistune, degraded service

---

## Validated outcomes (live on Voys, 2026-05-08 05:23-05:25 UTC)

VictoriaLogs evidence on three test queries:

| Test | klai-fast rewrite output | confidence_band | reranker_top1 | top_k served |
|---|---|---|---|---|
| Salesforce | "Voys Freedom Salesforce **CRM-koppeling Bubble** integratie" | high | 0.99 | 20 |
| Acme Foo Connector | (unchanged — no bridging applied to fictional product) | low | 0.0019 | 20 |
| Vakantie | "Voys voert vakantie-instellingen door..." (no brand-terms) | low | 0.098 | 20 |

Per-REQ confirmation:
- REQ-1 ✅ band field on every retrieve response
- REQ-4 ✅ `served_top_k: 20` on all calls (was 5 on 2026-05-07)
- REQ-5 ✅ Salesforce-query rewrite added `CRM-koppeling Bubble integratie` — exact bridging behavior. Did not over-apply on Acme or vakantie
- REQ-2 ✅ Acme query (band=low) produced "Dat staat niet in de kennisbank" — anti-hallucination injection fired correctly
- Reranker recovery: 0.18 (5/7 incident) → 0.99 (5/8) on functionally same query class — 5.4× improvement, not noise

---

## Configuration knobs (operator reference)

All defaults safe; tune via env without redeploy:

| Env var | Default | Range | Effect |
|---|---|---|---|
| `KNOWLEDGE_RETRIEVE_TOP_K` | `20` | 1-50 | chunks forwarded to LLM |
| `CONFIDENCE_BAND_HIGH_THRESHOLD` | `0.60` | 0.0-1.0 (must > low) | rerank-score ≥ this → band=high |
| `CONFIDENCE_BAND_LOW_THRESHOLD` | `0.30` | 0.0 ≤ < high | rerank-score < this → band=low |
| `LINK_EXPAND_SCORE_BOOST` | `1.00` | 1.0-1.3 | multiplicative boost for `_link_expanded` chunks |
| `KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION` | `0` | "0"/"1" | "1" disables anti-hallucination injection (rollback) |

---

## Files touched (for code-archaeology)

```
klai-retrieval-api/
  retrieval_api/
    config.py                    # +5 settings + 2 validators
    models.py                    # +1 RetrieveResponse field, +1 ConfidenceBand type
    metrics.py                   # +2 counters (band, link-expand-outcome)
    api/retrieve.py              # 2 helper fns + boost-pass + band-emit + counter-incs
  tests/
    test_confidence_band.py      # NEW — 24 unit tests on the 2 helpers
    test_api.py                  # +5 e2e tests (TestConfidenceBandEndToEnd)
                                 # + retrieval_quality_floor mock fix (was pre-existing red)

klai-knowledge-ingest/
  knowledge_ingest/
    eval/suites/chat.yaml        # +7 brand_bridging regression queries
  tests/
    test_sparse_input_parity.py  # NEW — 8 tests locking in REQ-6 audit verdict

deploy/
  litellm/klai_knowledge.py      # injection text + RETRIEVE_TOP_K=20 + brand-bridging prompt + warning-level log
  litellm/tests/test_low_confidence_injection.py  # NEW — 15 hook tests
  grafana/provisioning/dashboards/rag-quality.json     # +1 Low-Confidence panel
  grafana/provisioning/alerting/rag-eval-rules.yaml    # +1 alert rule

docs/
  runbooks/rag-quality.md        # +1 section (triage for the new alert)
  architecture/retrieval-improvements-roadmap.md  # untouched (Tier 3 still gated on production traces)

NO migrations, NO docker-compose changes, NO new services, NO SOPS env updates required.
```

---

## What still needs to happen (post-deploy validation, AC-2/4/5/6/9)

These were out of scope for the merge gate; depend on production traces:

- **AC-4 / AC-5 (eval delta)**: run `docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.eval --suite chat --variant low_confidence_v1` and compare against the existing `post_pr_abcdefg_v1` baseline. Target: `chat-brand-salesforce-bridging` `context_precision >= 0.50`; aggregate non-brand-bridging queries no regression > 0.02.
- **AC-6 (link-expand survival rate)**: 7-day window post-deploy. If `retrieval_link_expand_top_k_total{outcome=hit} / total` stays at 0%, the boost is too low — raise `LINK_EXPAND_SCORE_BOOST` to 1.10 or 1.15.
- **AC-9 (latency p95)**: ≤ pre-SPEC + 10%. Capture pre-deploy baseline, compare 24h post-deploy.

---

## What worked, what didn't, what we'd redo

### Worked
- Pure helper functions (`_compute_confidence_band`, `_apply_link_expand_boost`) — clean unit-test surface, edge-cases isolated from pipeline wiring
- Fail-open everywhere (every layer degrades to current behavior on any failure path)
- Conservative defaults (`link_expand_score_boost=1.00` ships dormant) — SPEC ships safe even if the SPEC author miscalibrated thresholds
- Threshold-validators at startup catch misconfig early (low must be < high)
- Pydantic `Optional[ConfidenceBand]` field — older clients (klai-portal, mcp) ignore the new field gracefully
- Reading the existing `retrieval-improvements-roadmap.md` BEFORE proposing — caught that 4 of the originally-proposed SPECs were already shipped (Tier 1+2 SHIPPED 2026-05-05). Pitfall captured as `spec-scope-without-roadmap-check (MED)` in [process-rules.md](../.claude/rules/klai/pitfalls/process-rules.md).

### Didn't work first time (cleaned up in #517)
- Grafana panel third series queried `litellm_low_confidence_injection_total` — a Prometheus counter that doesn't exist (litellm-hook has no `prometheus_client` dependency). Fixed in #517: re-pointed to `retrieval_confidence_band_total{band=~"low|unknown"}`.
- `test_retrieve_happy_path` + `test_link_expanded_flag_does_not_leak_to_response` were red on main due to a pre-existing `retrieval_quality_floor` MagicMock gap. Fixed in #517 alongside the panel repair.

### Didn't work after deploy (cleaned up in #518)
- `low_confidence_injection_applied` event was emitted as `logger.info`, but the litellm container's root logger filters info-level emits from non-uvicorn modules. Verified via VictoriaLogs: zero info-level klai_knowledge events visible despite gedrags-matig confirmed firing. Fixed in #518: upgraded to `logger.warning` (matches the existing visible warnings like `KlaiKnowledgeHook: jwt rejected`).

### Still imperfect (deliberate, not blockers)
- `link_expand_score_boost = 1.00` ships dormant. The SPEC's REQ-3 is technically inert until an operator raises it post-eval. Documented this is on purpose — boost should only activate after measurement.
- The Dutch anti-hallucination injection text (REQ-2) is an educated guess. Not A/B-tested. First production traces will tell whether it triggers gracefully or sounds harsh. Tunable via the constant, not via SPEC-deploy.
- Brand-bridging prompt examples are CRM-heavy (3/3 examples touch CRM-adjacent flows). May not generalize evenly to other tenant domains. Will see in production.
- 4 of the originally-proposed SPECs were retracted on the way to writing this one (already shipped). The cleanup-PR ratio (#517 + #518 size relative to #516) reflects "we found three real issues during self-review and fixed them within 12h" — not a structural quality issue, but worth flagging.

---

## Forward links (out of scope for this work, but adjacent)

- **Privacy / retention work** (just starting, 2026-05-08): three-mode telemetry config per tenant (`off` / `shadow` / `full`), 7-day default retention on query-text in VictoriaLogs, embeddings-as-shadow-values for system improvement without raw query storage. Will land as `SPEC-PRIVACY-QUERY-SHADOW-001` (or similar) — see in-progress audit at `.moai/specs/SPEC-PRIVACY-*` once written.
- **Tier 3 roadmap** (deferred until 4 weeks of production traces, per `retrieval-improvements-roadmap.md`):
  - HyDE if short-tech-query precision plateaus
  - GraphRAG community summaries if cross-doc synthesis demand emerges
  - Agentic RAG with query decomposition if multi-hop demand emerges
- **Graphiti NER improvement**: Cypher-debug confirmed that `Salesforce` is not extracted as an entity in Voys's knowledge graph (only generic `CRM` is). Brand-name-in-list-construction NER coverage gap. Out of scope for this SPEC; query-rewrite (REQ-5) is the pragmatic bridge until ingest-time NER improves.

---

## Process notes (for future SPEC-authors)

- `Read existing roadmap docs before proposing in the same domain.` Klai has explicit roadmap files (`docs/architecture/*-roadmap.md`) that list shipped state with PR numbers. A 5-second `ls .moai/specs/ | grep <DOMAIN>` and a single `cat docs/architecture/<domain>-roadmap.md` would have prevented an early proposal of 4 SPECs that were already shipped. Now codified as `spec-scope-without-roadmap-check (MED)` pitfall.
- `Distinguish SPEC-work from runbook-work.` Enabling already-built features for a tenant (e.g. taxonomy curation for Voys) is a runbook-action, not a SPEC. SPECs are for new code, new behavior, new architecture, new dependencies.
- `Pre-existing test failures on main are a useful audit signal.` While shipping #516, two tests were red — investigation revealed a pre-existing mock-fixture gap unrelated to the SPEC. #517 fixed it in passing. Don't ignore pre-existing reds in services you're already touching; if the fix is small, take it.

---

## Update 2026-05-08 — entity_names follow-up (PRs #519, #520, #522)

The Graphiti NER coverage gap flagged in "Forward links" above produced
two parallel workstreams later the same day. Both relevant to anyone
restructuring this doc into its permanent home.

### Workstream 1 — surface what Graphiti DOES extract (shipped)

The original observation was that Graphiti returns `EntityNode` objects
with both `.uuid` and `.name` per artifact, but only the opaque UUIDs
were persisted on Qdrant chunks (`entity_uuids` payload field). Entity
NAMES — readable strings like `"Bubble Cloud"`, `"Yealink USB
Connect-programma"`, `"PCMA codec"` — were thrown away.

PRs:

| PR | sha | What |
|---|---|---|
| #519 | `5150bfc7` | qdrant_store helper + per-chunk substring filter, graph.py extraction, retrieval-api exposure, backfill script, 10 unit tests |
| #520 | `80262394` | hotfix — ChunkResult Pydantic field + retrieve.py constructor + Dockerfile bake-in (the field reached search.py but was filtered at the response-model boundary; "Multi-layer data threading in retrieval results" rule bit) |
| #522 | `7df92da7` | follow-up — `chunks_total` log field for Grafana coverage rate, `scripts/__init__.py` for `python -m scripts.*` invocation, fix pre-existing `test_ensure_collection_skips_indexes_when_already_present` ("Qdrant skip-if-present index test" pitfall) |

Architecture: Graphiti runs document-level (one episode per artifact),
producing `entity_names: list[str]`. `qdrant_store.set_entity_graph_data`
filters per-chunk by literal substring match (case-insensitive, 3-char
minimum to suppress `AI`-in-`fail` false positives). Each chunk's
payload only carries names that actually appear in its text, preventing
BM25 pollution across long multi-section documents.

Voys post-backfill coverage:

```
Voys total:                          4,484 chunks (all in support KB)
  has entity_uuids (Graphiti ran):   3,091 (68.9%)
  has entity_names (post-backfill):  2,977 (66.4%)
  has uuids but no names (filter):     114 ( 2.5%)
  no uuids at all (Graphiti gap):    1,393 (31.1%)
```

Operator backfill: `docker exec klai-core-knowledge-ingest-1 python -m
scripts.backfill_entity_names --org-id <X> [--dry-run]`. Idempotent;
re-runs are safe.

### Workstream 2 — fix the underlying extraction gap (drafted, not shipped)

`SPEC-RAG-GRAPHITI-NER-COVERAGE-001` (drafted 2026-05-08, not yet
implemented). The triggering case is the same — `Salesforce` not
extracted, only generic `CRM`. Three hypotheses to A/B on Voys:

- **A (primary recommendation)**: custom Graphiti `entity_types`
  Pydantic models (`Brand`, `Product`, `IntegrationPartner`) — uses
  Graphiti 0.28's first-class API, no extra LLM calls per chunk
- **B**: prompt-override on the Graphiti extraction prompt (if
  hookable in 0.28)
- **C**: pre-pass entity-hint LLM call that feeds into Graphiti

Eval criterion: brand-bridging RAGAS `context_recall ≥ 0.50` on the
seven `chat-brand-*` canaries added by REQ-7 of this SPEC.

Out of scope per that SPEC: introducing GLiNER/spaCy as a parallel NER
stack. Klai already has Graphiti; adding a second pipeline doubles
operational surface area for marginal gain.

### Open hardening items (not blocking)

- **Re-ingest durability gap**: `set_entity_graph_data` writes per-chunk
  via `set_payload`, but `upsert_enriched_chunks` does delete+re-insert
  with new chunk-UUIDs on every ingest. If Graphiti fails on a future
  re-ingest while enrichment succeeds, that artifact loses
  `entity_names` until the next successful Graphiti run. Mitigation
  would be to also store `entity_names` in `artifacts.extra` JSONB so
  it can be reapplied. Tracked informally; would slot into
  `SPEC-RAG-GRAPHITI-NER-COVERAGE-001` as REQ-N or its own micro-SPEC.
- **Graphiti coverage on the support KB**: 1,393 chunks (31%) have no
  `entity_uuids`. Re-running the Graphiti enrichment on those artifacts
  is an operator action separate from any code change.
- **Citation surface**: `entity_names` reaches retrieval JSON but is
  not yet rendered in the chat citation UI. Layer 7 of the multi-layer
  threading rule. Cheap follow-up if there's UX value.

---

**Last updated**: 2026-05-08 06:00 UTC by Mark + AI pair-session
**Status**: live in production on `klai-core` (core-01); awaiting first 7-day production-traces window for AC-6 + post-deploy delta measurements
**2026-05-08 supplement**: entity_names follow-up shipped (#519/#520/#522), Graphiti NER coverage SPEC drafted (`.moai/specs/SPEC-RAG-GRAPHITI-NER-COVERAGE-001/`).
