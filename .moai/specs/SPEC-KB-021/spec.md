---
id: SPEC-KB-021
version: 0.1.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

- 2026-04-16 — v0.1.0 — Mark Vletter — Initial draft. Scope: multi-source retrieval quality voor orgs met 4+ knowledge bases. Drie gecoördineerde veranderingen (source-aware enrichment, source quota, query router) elk achter een eigen feature flag met shadow-mode en activation gate op een 50-query Voys evaluation set.

---

## 1. Context

Klai-orgs hebben steeds vaker meerdere knowledge sources naast elkaar. Het Voys-voorbeeld: `voys-help`, `voys-wiki`, `voys-notion`, `redcactus-wiki`, `mitel-helpcenter`, `ascend-helpcenter` — zes bronnen onder één org. De huidige architectuur gebruikt één Qdrant collection met een `kb_slug` payload filter. Dat is de officieel aanbevolen Qdrant multi-tenancy aanpak en die laten we staan. Maar de retrieval pipeline mist drie dingen:

1. **Bron-bewustzijn in het context-prefix.** De embedder weet niet uit welke "wereld" een chunk komt. Mitel's "hunt group", Voys's "belgroep" en Ascend's equivalent embedden nu vrijwel identiek zonder source-context. Vocabulary-drift tussen bronnen degradeert de retrieval.
2. **Diversiteit in de top-K.** Eén bron kan alle 5 slots monopoliseren. Met 6 bronnen en `top_k=5` is het wiskundig gegarandeerd dat minstens één bron afwezig is. Er is nu geen MMR, geen quota.
3. **Automatische bronselectie wanneer `kb_slugs=null`.** De gebruiker moet handmatig kiezen uit tot 6 KBs via de KBScopeBar. Dat wordt cognitief zwaar voorbij de 4 KBs.

Deze SPEC introduceert drie gecoördineerde veranderingen, elk achter een eigen feature flag, die we onafhankelijk kunnen activeren nadat een 50-query Voys-representatieve eval set statistisch significante verbetering laat zien.

## 2. Goals / Non-goals

### Goals

- Retrieval kwaliteit (Context Precision, NDCG@10, Faithfulness) meetbaar verbeteren voor orgs met ≥ 4 knowledge bases.
- Bronvocabulaire laten meewegen in embeddings via een uitgebreide enrichment prompt.
- Bron-diversiteit in top-K garanderen zonder query-intent te negeren (dynamische bypass bij expliciete bronvermelding).
- Automatische KB-selectie wanneer de gebruiker geen scope kiest en de org ≥ 4 KBs heeft.
- Alle drie de veranderingen moeten onafhankelijk activeerbaar zijn, elk met shadow-mode en een harde activation gate op de evaluation set.

### Non-goals

- Multi-collection split in Qdrant (strijdig met de officiële Qdrant multi-tenancy aanpak).
- Vervangen van de huidige `BAAI/bge-reranker-v2-m3` reranker.
- Query decompositie voor multi-hop queries (ander probleem, Azure Agentic Retrieval territorium).
- Auto-routing binnen de LiteLLM hook (hoort in retrieval-api thuis, niet in de proxy).
- Geforceerd her-embedden van alle bestaande chunks (selectief via `prefix_template_version` later).

## 3. Scope / Out of scope

### In scope

- `klai-knowledge-ingest/` — uitbreiden van de enrichment prompt, threading van `kb_name` / `connector_type` / `source_domain`, toevoegen van `content_type` aan de `EnrichmentResult`, template versioning.
- `klai-retrieval-api/` — nieuwe `services/diversity.py` (source quota), nieuwe `services/router.py` (3-layer query router), uitbreiden `ChunkResult` model met `kb_slug`, uitbreiden `RetrieveMetadata` met observability-velden, config flags in `config.py`.
- `klai-portal/backend/app/models/knowledge_bases.py` — verifieer dat `description` veld aanwezig is en koppel een minimum-lengte validatie (10 chars) voor nieuwe KBs in het frontend formulier. Bestaande KBs blijven werken met `null` description.

### Out of scope

- Glossary-injectie voor vendor-specifieke terminologie (Mitel "hunt group" → Voys "belgroep"). Eerst meten of bron-bewust prefix de kloof sluit.
- MMR binnen de source quota. Eerst quota-only; MMR als follow-up indien intra-source redundantie meetbaar blijkt.
- Router Layer 3 (LLM fallback) standaard aan bij launch — default off, pas activeren na shadow-analyse op Layer 1+2.
- Automatische KB-description generatie uit ingested content — description is gebruikersinput bij KB-creatie.
- Forceerd her-embedden van alle bestaande chunks bij een nieuwe prefix template.

## 4. EARS Requirements

### REQ-INGEST — Source-aware enrichment prompt

- **REQ-INGEST-001 (Ubiquitous):** The knowledge-ingest service **shall** generate a context_prefix that includes `kb_name`, `connector_type`, `source_domain`, document title, and domain-specific terminology hints for every ingested chunk.
- **REQ-INGEST-002 (Event-Driven):** **When** a chunk is enriched by `enrich_chunk`, the system **shall** populate `EnrichmentResult.content_type` with exactly one of `procedural | conceptual | reference | warning | example`.
- **REQ-INGEST-003 (Ubiquitous):** The enrichment prompt **shall** cap the generated context_prefix at 120 tokens, following Anthropic Contextual Retrieval guidance.
- **REQ-INGEST-004 (State-Driven):** **While** `config.prefix_template_version == 2`, the system **shall** write that version into the Qdrant chunk payload so later selective re-embed is possible.
- **REQ-INGEST-005 (Event-Driven):** **When** an `IngestRequest` is received, the system **shall** thread `kb_slug`, `kb_name`, and `connector_type` through `ingest_tasks.extra_payload` to `enrich_chunk`.
- **REQ-INGEST-006 (If-Unwanted):** **If** the LLM call in `enrich_chunk` fails or returns an invalid JSON, **then** the system **shall** fall back to a deterministic prefix template (`"{kb_name}: {title}"`) and set `content_type=reference` so ingestion never blocks on enrichment failure.

### REQ-DIVERSITY — Source quota post-rerank

- **REQ-DIVERSITY-001 (Ubiquitous):** The retrieval-api **shall** expose a function `source_quota_select(reranked, query_resolved, top_n, max_per_source)` in `klai-retrieval-api/retrieval_api/services/diversity.py`.
- **REQ-DIVERSITY-002 (State-Driven):** **While** `config.source_quota_enabled == True`, the system **shall** apply `source_quota_select` after the reranker and before `quality_boost` in `retrieve.py`.
- **REQ-DIVERSITY-003 (Event-Driven):** **When** `source_quota_select` is invoked with `max_per_source=2`, the system **shall** greedily select chunks sorted by `reranker_score` descending while enforcing that no `kb_slug` appears more than `max_per_source` times in the returned list.
- **REQ-DIVERSITY-004 (State-Driven):** **While** `config.source_quota_bypass_on_mention == True` and a lowercase `kb_slug` (len > 3) appears as a substring in `query_resolved.lower()`, the system **shall** skip the quota and return `reranked[:top_n]` unchanged.
- **REQ-DIVERSITY-005 (If-Unwanted):** **If** after quota enforcement fewer than `top_n` results remain, **then** the system **shall** fall back to the original reranker score order to fill the remaining slots.
- **REQ-DIVERSITY-006 (Ubiquitous):** The `ChunkResult` model in `klai-retrieval-api/retrieval_api/models.py` **shall** include a `kb_slug: str | None = None` field so the quota algorithm can read the source without re-fetching Qdrant.
- **REQ-DIVERSITY-007 (State-Driven):** **While** `config.source_quota_enabled == False` and the request contains `force_shadow == True`, the system **shall** compute the quota decision, log it in `RetrieveMetadata`, and return the unmodified reranked result.

### REQ-ROUTER — Three-layer query router

- **REQ-ROUTER-001 (Event-Driven):** **When** `req.kb_slugs is None` and `org.kb_count >= config.router_min_kb_count` and `req.scope in {"org", "both"}` and `config.router_enabled == True`, the system **shall** invoke `route_to_kbs(query_resolved, query_vector, org_id, kb_catalog)` in `klai-retrieval-api/retrieval_api/services/router.py`.
- **REQ-ROUTER-002 (State-Driven):** **While** the router is invoked, the system **shall** execute Layer 1 (keyword gate) first using a pre-computed `{brand_term → kb_slug}` map derived from KB name and KB description, applying exact lowercase substring matching.
- **REQ-ROUTER-003 (If-Unwanted):** **If** Layer 1 returns no match, **then** the system **shall** execute Layer 2 (semantic margin) by computing cosine similarity between `query_vector` and pre-computed centroids of `"{kb_name}. {kb_description}"` per KB, applying thresholds `router_margin_single` and `router_margin_dual`.
- **REQ-ROUTER-004 (State-Driven):** **While** `config.router_llm_fallback == True` and Layer 1 produced no match and Layer 2 margin is below `router_margin_dual`, the system **shall** execute Layer 3 (LLM fallback) via the klai-fast model with a 500ms timeout.
- **REQ-ROUTER-005 (If-Unwanted):** **If** Layer 3 times out or fails, **then** the system **shall** return a `RoutingDecision` with `selected_kbs=None` (fail-open, no filter applied).
- **REQ-ROUTER-006 (Unwanted):** The system **shall not** invoke the router when `req.kb_slugs is not None` — user-provided scope always takes precedence, with zero router latency and zero router log entries.
- **REQ-ROUTER-007 (State-Driven):** **While** the centroid cache is warm for `org_id` and the cached entry is younger than `router_centroid_ttl_seconds`, the system **shall** reuse the cached centroids instead of recomputing them.
- **REQ-ROUTER-008 (Ubiquitous):** The router **shall** inject its decision into `search.hybrid_search` as the `kb_slugs` parameter when `selected_kbs` is non-empty.

### REQ-OBSERVABILITY — Logging across all three changes

- **REQ-OBSERVABILITY-001 (Ubiquitous):** The retrieval-api **shall** log `quota_applied: bool`, `quota_per_source_counts: dict[str, int]`, and `quota_bypass_reason: str | None` in `RetrieveMetadata` whenever the quota path is evaluated (including shadow-mode).
- **REQ-OBSERVABILITY-002 (Ubiquitous):** The retrieval-api **shall** log `router_decision: list[str] | None`, `router_layer_used: "keyword" | "semantic" | "llm" | "skipped"`, `router_margin: float | None`, and `router_centroid_cache_hit: bool` in `RetrieveMetadata` whenever the router path is evaluated.
- **REQ-OBSERVABILITY-003 (Ubiquitous):** The knowledge-ingest service **shall** log `prefix_template_version` and `content_type` for every enriched chunk in the Qdrant payload so downstream consumers can verify which prompt version produced a chunk.
- **REQ-OBSERVABILITY-004 (Event-Driven):** **When** the router LLM fallback is invoked, the system **shall** log the latency and the fallback outcome (match / timeout / parse_error) so shadow-analysis can detect degradation.

### REQ-ROLLOUT — Feature flag + shadow-mode activation gate

- **REQ-ROLLOUT-001 (Ubiquitous):** Each of the three changes **shall** be gated by an independent feature flag — `source_quota_enabled`, `router_enabled`, `prefix_template_version` — that defaults to the inactive / v1 state at deploy time.
- **REQ-ROLLOUT-002 (State-Driven):** **While** a flag is in shadow-mode (enabled=False but `force_shadow=True`), the system **shall** compute the decision, log it in `RetrieveMetadata`, and return the unmodified baseline result.
- **REQ-ROLLOUT-003 (Ubiquitous):** Activation of any flag **shall** require: a ≥ 5% improvement on Context Precision (paired Wilcoxon signed-rank, p < 0.05) **and** non-regression on NDCG@10, measured via `klai-retrieval-api/evaluation/eval_runner.py` on a 50-query Voys-representative set.
- **REQ-ROLLOUT-004 (Unwanted):** The system **shall not** flip more than one of the three flags in a single deploy — each activation requires its own evaluation run and its own deploy window.
- **REQ-ROLLOUT-005 (If-Unwanted):** **If** after activation a regression is detected on any of (Context Precision, NDCG@10, Faithfulness, quality_boost effectiveness), **then** the system **shall** be rolled back by flipping the flag off; shadow-mode remains available for forensic analysis.

## 5. Exclusions (What NOT to Build)

- **Glossary-injectie voor vendor-synoniemen.** Bijvoorbeeld "hunt group" → "belgroep" of Mitel-error-codes → Voys-terminologie. Eerst meten of het bron-bewuste prefix alleen al voldoende verbetering geeft; pas daarna overwegen.
- **MMR binnen source quota.** Alleen quota in MVP — geen Maximal Marginal Relevance op chunk-niveau binnen één bron. Follow-up indien intra-source redundantie zichtbaar wordt in de shadow-logs.
- **Router Layer 3 (LLM fallback) bij launch.** Default `router_llm_fallback=False`. Alleen inschakelen nadat Layer 1+2 shadow-analyse aantoont dat ze > 10% van de queries onbeslist laten.
- **Automatische KB-description generatie.** `description` is en blijft gebruikersinput bij KB-creatie; het systeem genereert deze niet uit de ingested content.
- **Geforceerd her-embedden van alle bestaande chunks voor de nieuwe prefix-template.** Selectief her-embedden is mogelijk via `prefix_template_version` in de payload, maar niet verplicht.
- **Multi-collection split in Qdrant.** Strijdig met de officiële Qdrant multi-tenancy aanpak en met onze huidige architectuur.
- **Vervangen van `BAAI/bge-reranker-v2-m3`.** De reranker blijft zoals hij is.
- **Query decompositie voor multi-hop queries.** Ander probleem, ander SPEC.
- **Auto-routing binnen de LiteLLM hook.** Routing hoort thuis in retrieval-api, niet in de proxy.
- **Parallel flag-flips.** Nooit meer dan één van de drie flags in één deploy activeren.

## 6. Related SPECs

- **SPEC-KB-008** — retrieval-api split. Foundation, geen conflict.
- **SPEC-KB-014** — gap detection. Router kan de gap-rate beïnvloeden (minder onjuiste bronnen in context). **Moet gemeten worden** op de eval-set: gap-rate mag niet stijgen.
- **SPEC-KB-015** — self-learning `quality_boost`. Quota wijzigt de volgorde **vóór** `quality_boost`. De bestaande pipeline in `retrieve.py` bewaart de relatieve volgorde van de quality-boost stap bovenop de quota-output; wire-volgorde: reranker → source_quota → quality_boost.
- **SPEC-KB-022** — Taxonomy V2 multi-label tagging. Raakt `enrichment.py` aan. **Cross-dependency:** ons `content_type` veld kan overlappen of complementeren met taxonomy tags. Beide SPECs moeten hetzelfde `EnrichmentResult` model uitbreiden zonder veldconflict. Coördinatie: `content_type` is een Literal enum met 5 waarden (`procedural|conceptual|reference|warning|example`), taxonomy tags zijn een aparte `list[str]` — geen naamconflict.
- **SPEC-KB-023** — Taxonomy Discovery blind labeling at ingest. Raakt dezelfde `enrichment.py`. Design voor coexistence: beide SPECs lezen dezelfde `IngestRequest` en schrijven in dezelfde `EnrichmentResult` — enforce alfabetische veldvolgorde in model en houd velden niet-overlappend.
- **SPEC-KB-024** — Taxonomy Discovery embedding clustering. Ingest-side clustering. Geen direct conflict — draait na enrichment.
- **SPEC-CRAWLER-003** — link graph authority boost. **Authority boost is PRE-rerank, quota is POST-rerank.** Ze componeren cleanly: authority beïnvloedt reranker-input, quota beïnvloedt reranker-output.
- **SPEC-EVIDENCE-001** — 150-query RAGAS eval framework. **REUSE** voor de activation gate. We voegen 50 Voys-representatieve queries toe aan `evaluation/test_queries_curated.json` en draaien `eval_runner.py` met de bestaande RAGAS-metrics.

## 7. Delta markers (brownfield)

- **[EXISTING]** `klai-knowledge-ingest/knowledge_ingest/enrichment.py` — `ENRICHMENT_PROMPT` op regel 22-41, `EnrichmentResult` pydantic model, `enrich_chunk` async functie. @MX:TODO verify exact line numbers during /moai run.
- **[MODIFY]** `klai-knowledge-ingest/knowledge_ingest/enrichment.py` — uitbreiden `ENRICHMENT_PROMPT` met source-aware context, toevoegen `content_type: Literal[...]` aan `EnrichmentResult`, bouwen van deterministische fallback prefix bij LLM-failure.
- **[MODIFY]** `klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py` — threading van `kb_name`, `connector_type`, `source_domain` via `extra_payload`.
- **[MODIFY]** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` — nieuwe velden op `IngestRequest` (`kb_name`, `connector_type`, `source_domain`).
- **[MODIFY]** `klai-knowledge-ingest/knowledge_ingest/config.py` — `prefix_template_version: int = 2` default.
- **[NEW]** `klai-retrieval-api/retrieval_api/services/diversity.py` — `source_quota_select`.
- **[NEW]** `klai-retrieval-api/retrieval_api/services/router.py` — `route_to_kbs`, 3-layer implementatie, centroid cache.
- **[MODIFY]** `klai-retrieval-api/retrieval_api/models.py` — toevoegen `kb_slug: str | None = None` aan `ChunkResult` (regel 21-41). Uitbreiden `RetrieveMetadata` met observability-velden.
- **[MODIFY]** `klai-retrieval-api/retrieval_api/api/retrieve.py` — quota-hook tussen rerank-output (rond regel 168) en `quality_boost` (rond regel 177); router-hook na coreference + gate, vóór `search.hybrid_search` (rond regel 63-94). @MX:TODO verify exact line numbers during /moai run.
- **[MODIFY]** `klai-retrieval-api/retrieval_api/config.py` — nieuwe flags: `source_quota_enabled`, `source_quota_max_per_source`, `source_quota_bypass_on_mention`, `router_enabled`, `router_min_kb_count`, `router_margin_single`, `router_margin_dual`, `router_llm_fallback`, `router_centroid_ttl_seconds`.
- **[EXISTING]** `klai-portal/backend/app/models/knowledge_bases.py` — `PortalKnowledgeBase.description` veld op regel 22. @MX:TODO verify during /moai run.
- **[MODIFY]** `klai-portal/backend/app/models/knowledge_bases.py` + frontend KB-formulier — minimum lengte 10 chars voor `description` bij nieuwe KBs; bestaande KBs mogen `null` houden.
- **[MODIFY]** `klai-retrieval-api/evaluation/test_queries_curated.json` — toevoegen van 50 Voys-representatieve queries met ground-truth `kb_slug` en chunk-ids.
- **[REUSE]** `klai-retrieval-api/evaluation/eval_runner.py` — geen wijziging, gebruik bestaand framework voor de activation gate.
