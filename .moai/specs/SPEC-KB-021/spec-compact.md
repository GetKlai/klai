# SPEC-KB-021 — Compact Reference

Auto-extracted from spec.md + acceptance.md. EARS requirements, acceptance criteria, files-to-modify, and exclusions only.

## EARS Requirements

### REQ-INGEST — Source-aware enrichment prompt

- **REQ-INGEST-001:** The knowledge-ingest service **shall** generate a context_prefix that includes `kb_name`, `connector_type`, `source_domain`, document title, and domain-specific terminology hints for every ingested chunk.
- **REQ-INGEST-002:** **When** a chunk is enriched by `enrich_chunk`, the system **shall** populate `EnrichmentResult.content_type` with exactly one of `procedural | conceptual | reference | warning | example`.
- **REQ-INGEST-003:** The enrichment prompt **shall** cap the generated context_prefix at 120 tokens.
- **REQ-INGEST-004:** **While** `config.prefix_template_version == 2`, the system **shall** write that version into the Qdrant chunk payload.
- **REQ-INGEST-005:** **When** an `IngestRequest` is received, the system **shall** thread `kb_slug`, `kb_name`, and `connector_type` through `ingest_tasks.extra_payload` to `enrich_chunk`.
- **REQ-INGEST-006:** **If** the LLM call in `enrich_chunk` fails or returns invalid JSON, **then** the system **shall** fall back to a deterministic prefix `"{kb_name}: {title}"` with `content_type=reference`.

### REQ-DIVERSITY — Source quota post-rerank

- **REQ-DIVERSITY-001:** The retrieval-api **shall** expose `source_quota_select(reranked, query_resolved, top_n, max_per_source)` in `klai-retrieval-api/retrieval_api/services/diversity.py`.
- **REQ-DIVERSITY-002:** **While** `config.source_quota_enabled == True`, the system **shall** apply `source_quota_select` after the reranker and before `quality_boost`.
- **REQ-DIVERSITY-003:** **When** invoked with `max_per_source=2`, the system **shall** greedily select by `reranker_score` desc with per-source cap.
- **REQ-DIVERSITY-004:** **While** a lowercase `kb_slug` (len > 3) appears as substring in `query_resolved.lower()`, the system **shall** bypass quota and return `reranked[:top_n]`.
- **REQ-DIVERSITY-005:** **If** fewer than `top_n` results remain after quota, **then** the system **shall** fall back to original score order.
- **REQ-DIVERSITY-006:** `ChunkResult` **shall** include `kb_slug: str | None = None`.
- **REQ-DIVERSITY-007:** **While** `enabled=False` and `force_shadow=True`, the system **shall** compute quota, log in metadata, return unmodified reranked.

### REQ-ROUTER — Three-layer query router

- **REQ-ROUTER-001:** **When** `req.kb_slugs is None` and `org.kb_count >= router_min_kb_count` and `req.scope in {"org","both"}` and `router_enabled == True`, the system **shall** invoke `route_to_kbs`.
- **REQ-ROUTER-002:** The router **shall** execute Layer 1 (keyword gate) first using a pre-computed `{brand_term → kb_slug}` map.
- **REQ-ROUTER-003:** **If** Layer 1 returns no match, **then** the system **shall** execute Layer 2 (semantic margin) against pre-computed KB centroids.
- **REQ-ROUTER-004:** **While** `router_llm_fallback == True` and Layers 1-2 are inconclusive, the system **shall** execute Layer 3 via klai-fast with 500ms timeout.
- **REQ-ROUTER-005:** **If** Layer 3 times out or fails, **then** the system **shall** return `selected_kbs=None` (fail-open).
- **REQ-ROUTER-006:** The system **shall not** invoke the router when `req.kb_slugs is not None`.
- **REQ-ROUTER-007:** **While** the centroid cache is warm and younger than TTL, the system **shall** reuse cached centroids.
- **REQ-ROUTER-008:** The router **shall** inject its decision as `kb_slugs` into `search.hybrid_search` when `selected_kbs` is non-empty.

### REQ-OBSERVABILITY — Logging across all three changes

- **REQ-OBSERVABILITY-001:** The retrieval-api **shall** log `quota_applied`, `quota_per_source_counts`, `quota_bypass_reason` in `RetrieveMetadata`.
- **REQ-OBSERVABILITY-002:** The retrieval-api **shall** log `router_decision`, `router_layer_used`, `router_margin`, `router_centroid_cache_hit` in `RetrieveMetadata`.
- **REQ-OBSERVABILITY-003:** The knowledge-ingest service **shall** log `prefix_template_version` and `content_type` for every enriched chunk in the Qdrant payload.
- **REQ-OBSERVABILITY-004:** **When** the router LLM fallback is invoked, the system **shall** log its latency and outcome.

### REQ-ROLLOUT — Feature flag + shadow-mode activation gate

- **REQ-ROLLOUT-001:** Each change **shall** be gated by an independent feature flag defaulting to inactive.
- **REQ-ROLLOUT-002:** **While** a flag is in shadow-mode (enabled=False + force_shadow=True), the system **shall** compute the decision, log it, and return the baseline.
- **REQ-ROLLOUT-003:** Activation **shall** require ≥ 5% Context Precision improvement (Wilcoxon signed-rank, p < 0.05) and NDCG@10 non-regression on a 50-query Voys set.
- **REQ-ROLLOUT-004:** The system **shall not** flip more than one of the three flags in a single deploy.
- **REQ-ROLLOUT-005:** **If** regression is detected post-activation, **then** the system **shall** be rolled back by flipping the flag off.

## Acceptance Criteria

### Change 1 — Source-aware enrichment

- **AC-INGEST-1.1 (Happy path):** Given kb_slug="voys-help" + connector_type="webscrape", When enrich_chunk runs, Then context_prefix contains kb_name + connector + domain, content_type is one of 5 literals, token-count ≤ 120, prefix_template_version=2 in payload.
- **AC-INGEST-1.2 (LLM failure):** Given LLM timeout/invalid JSON, When enrich_chunk runs, Then ingestion does not block, context_prefix equals `"{kb_name}: {title}"`, content_type=reference, payload still written.
- **AC-INGEST-1.3 (Enum validation):** Given invalid content_type from LLM, When parsed, Then pydantic rejects, fallback path taken.

### Change 2 — Source quota post-rerank

- **AC-DIVERSITY-2.1 (Happy path):** Given 10 chunks across 3 sources, When quota with max=2 runs, Then 5 returned, no source > 2, quota_applied=True.
- **AC-DIVERSITY-2.2 (Dynamic bypass):** Given query mentions "mitel", When quota runs, Then reranked[:5] unchanged, quota_bypass_reason set.
- **AC-DIVERSITY-2.3 (Fallback underfill):** Given only 4 chunks, all same source, When quota runs, Then 4 returned, score-desc order.
- **AC-DIVERSITY-2.4 (Shadow-mode):** Given enabled=False + force_shadow=True, When pipeline runs, Then baseline returned, simulated quota logged.

### Change 3 — Query router

- **AC-ROUTER-3.1 (User override):** Given req.kb_slugs non-null, When pipeline runs, Then router NOT invoked, zero latency, user slugs preserved.
- **AC-ROUTER-3.2 (Layer 1 keyword):** Given "mitel" in query + matching KB, When router runs, Then layer_used=keyword, selected_kbs=["mitel-helpcenter"].
- **AC-ROUTER-3.3 (Layer 2 dual):** Given similarities 0.72/0.65/0.55 + thresholds 0.15/0.08, When router runs, Then dual-route, layer_used=semantic.
- **AC-ROUTER-3.4 (Layer 3 fail-open):** Given LLM timeout, When Layer 3 runs, Then selected_kbs=None, call does NOT fail.
- **AC-ROUTER-3.5 (Cache hit):** Given warm cache < TTL, When second request, Then router_centroid_cache_hit=True, no embed call.

### Shadow-mode

- **AC-SHADOW-1:** Given all flags off + force_shadow=True, When pipeline runs, Then metadata contains simulated quota + router fields, actual result is baseline.

### Performance

- **AC-PERF-1:** Router overhead ≤ 20ms p95 with warm centroid cache on Layer 2 path.
- **AC-PERF-2:** Layer 3 total extra-latency ≤ 510ms with hard 500ms timeout.

### Activation gate

- **AC-GATE-1:** Flag activates only when Context Precision improves ≥ 5% (paired Wilcoxon, p < 0.05) AND NDCG@10 non-regression on 50 Voys queries.
- **AC-GATE-2:** One flag per deploy window — never two flags together.

## Files to modify

### klai-knowledge-ingest

- `klai-knowledge-ingest/knowledge_ingest/enrichment.py` — ENRICHMENT_PROMPT (rond regel 22-41), EnrichmentResult + content_type literal, fallback template.
- `klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py` — thread kb_name, connector_type, source_domain via extra_payload.
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` — nieuwe velden op IngestRequest.
- `klai-knowledge-ingest/knowledge_ingest/config.py` — `prefix_template_version: int = 2`.

### klai-retrieval-api

- `klai-retrieval-api/retrieval_api/services/diversity.py` — **NEW**, `source_quota_select`.
- `klai-retrieval-api/retrieval_api/services/router.py` — **NEW**, `route_to_kbs` + 3-layer + centroid cache.
- `klai-retrieval-api/retrieval_api/models.py` — `ChunkResult.kb_slug` (rond regel 21-41), RetrieveMetadata observability velden, RoutingDecision model.
- `klai-retrieval-api/retrieval_api/api/retrieve.py` — quota-hook tussen rerank (~regel 168) en quality_boost (~regel 177); router-hook na coreference + gate, vóór hybrid_search (~regel 63-94).
- `klai-retrieval-api/retrieval_api/config.py` — flags: source_quota_enabled / max_per_source / bypass_on_mention, router_enabled / min_kb_count / margin_single / margin_dual / llm_fallback / centroid_ttl_seconds.
- `klai-retrieval-api/evaluation/test_queries_curated.json` — 50 Voys-representatieve queries met ground-truth.

### klai-portal

- `klai-portal/backend/app/models/knowledge_bases.py` — verifieer `description` veld (rond regel 22), enforce min 10 chars voor nieuwe KBs.
- Frontend KB-creatie formulier — min 10 chars description validatie voor nieuwe KBs.

## Exclusions

- Glossary-injectie voor vendor-synoniemen (first measure source-aware prefix effect).
- MMR binnen source quota (MVP is quota-only; MMR als follow-up).
- Router Layer 3 bij launch default aan (default off; activate only after Layer 1+2 shadow shows > 10% queries undecided).
- Automatische KB-description generatie (blijft gebruikersinput).
- Geforceerd her-embedden van bestaande chunks (selectief via prefix_template_version).
- Multi-collection split in Qdrant.
- Vervanging van BAAI/bge-reranker-v2-m3.
- Query decompositie voor multi-hop queries.
- Auto-routing in LiteLLM hook.
- Parallel flag-flips (één flag per deploy).
