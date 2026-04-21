# SPEC-KB-021 — Acceptance Criteria

Minimum 2 Given/When/Then scenarios per change (3 changes × 2+ = 6+ total). Plus shadow-mode, performance, en activation-gate criteria.

## Change 1 — Source-aware enrichment prompt

### AC-INGEST-1.1 — Happy path

**Given** een ingest request met `kb_slug="voys-help"`, `kb_name="Voys Helpdesk"`, `connector_type="webscrape"`, `source_domain="help.voys.nl"`, en een chunk over "belgroep instellen"
**When** `enrich_chunk` wordt aangeroepen
**Then** `EnrichmentResult.context_prefix` bevat "Voys Helpdesk" én "webscrape" én "help.voys.nl"
**And** `EnrichmentResult.content_type` is exact één van `["procedural", "conceptual", "reference", "warning", "example"]`
**And** `EnrichmentResult.context_prefix` token-count is ≤ 120
**And** de Qdrant payload die voor deze chunk wordt geschreven bevat `prefix_template_version == 2`

### AC-INGEST-1.2 — LLM-failure fallback

**Given** een ingest request zoals in AC-INGEST-1.1
**And** de enrichment LLM-call throwt een timeout-exception of retourneert ongeldige JSON
**When** `enrich_chunk` wordt aangeroepen
**Then** ingestie blokkeert niet
**And** `EnrichmentResult.context_prefix` is gelijk aan `"Voys Helpdesk: {title}"` (deterministisch fallback template)
**And** `EnrichmentResult.content_type == "reference"`
**And** de Qdrant payload wordt geschreven met de fallback-prefix en `prefix_template_version == 2`

### AC-INGEST-1.3 — Content-type enum validatie

**Given** een LLM-respons met `"content_type": "something_else"`
**When** `EnrichmentResult` wordt geparsed
**Then** pydantic rejecteert de waarde
**And** de fallback-pad (AC-INGEST-1.2) wordt genomen

## Change 2 — Source quota post-rerank

### AC-DIVERSITY-2.1 — Happy path, quota distribueert over bronnen

**Given** een reranked lijst van 10 chunks met `kb_slug` verdeling: `voys-help × 6`, `voys-wiki × 2`, `mitel-helpcenter × 2`, allemaal gesorteerd op reranker_score desc
**And** config `source_quota_enabled=True`, `source_quota_max_per_source=2`, `source_quota_bypass_on_mention=True`
**And** query_resolved = "hoe maak ik een nieuwe gebruiker aan" (geen kb_slug-vermelding)
**When** `source_quota_select(reranked, query_resolved, top_n=5, max_per_source=2)` wordt aangeroepen
**Then** de returnlijst heeft lengte 5
**And** geen `kb_slug` komt meer dan 2 keer voor
**And** de volgorde respecteert reranker_score desc binnen de quota-constraints
**And** `RetrieveMetadata.quota_applied == True`
**And** `RetrieveMetadata.quota_per_source_counts == {"voys-help": 2, "voys-wiki": 2, "mitel-helpcenter": 1}` (of equivalente verdeling)

### AC-DIVERSITY-2.2 — Dynamic bypass bij kb_slug mention

**Given** dezelfde reranked lijst als AC-DIVERSITY-2.1
**And** query_resolved = "mitel error X025 oplossen"
**When** `source_quota_select` wordt aangeroepen
**Then** de returnlijst is `reranked[:5]` ongewijzigd
**And** `RetrieveMetadata.quota_applied == False`
**And** `RetrieveMetadata.quota_bypass_reason == "kb_slug_mentioned:mitel-helpcenter"`

### AC-DIVERSITY-2.3 — Fallback bij onderbezetting

**Given** een reranked lijst van 4 chunks, alle van `kb_slug="voys-help"`
**And** config `source_quota_enabled=True`, `max_per_source=2`, `top_n=5`
**When** `source_quota_select` wordt aangeroepen
**Then** de returnlijst heeft lengte 4 (niet 5 — er zijn geen extra bronnen)
**And** de volgorde is reranker_score desc
**And** `RetrieveMetadata.quota_per_source_counts == {"voys-help": 4}`

### AC-DIVERSITY-2.4 — Shadow-mode

**Given** config `source_quota_enabled=False`
**And** request-header `force_shadow=True`
**And** dezelfde reranked lijst als AC-DIVERSITY-2.1
**When** de retrieve-pipeline draait
**Then** de daadwerkelijk gereturnde lijst is `reranked[:5]` (quota NIET toegepast)
**And** `RetrieveMetadata.quota_applied == False`
**And** `RetrieveMetadata.quota_per_source_counts` bevat de gesimuleerde quota-verdeling (wat het WEL geweest zou zijn)

## Change 3 — Three-layer query router

### AC-ROUTER-3.1 — User override respecteren

**Given** een request met `req.kb_slugs = ["voys-help"]`
**And** config `router_enabled=True`, `router_min_kb_count=4`
**And** org heeft 6 KBs
**When** de retrieve-pipeline draait
**Then** `route_to_kbs` wordt NIET aangeroepen
**And** `RetrieveMetadata.router_layer_used` is afwezig of `"skipped"` zonder latency-kosten
**And** `search.hybrid_search` ontvangt `kb_slugs=["voys-help"]` (user input ongewijzigd)

### AC-ROUTER-3.2 — Layer 1 keyword hit

**Given** een org met 6 KBs inclusief een KB met `name="Mitel Helpcenter"`, `description="Documentatie voor Mitel-telefoons en MiVoice-errors"`
**And** request met `req.kb_slugs=None`, `req.scope="org"`, query_resolved="hoe configureer ik mitel voip"
**And** config `router_enabled=True`, `router_min_kb_count=4`
**When** `route_to_kbs` wordt aangeroepen
**Then** `RoutingDecision.selected_kbs == ["mitel-helpcenter"]`
**And** `RoutingDecision.layer_used == "keyword"`
**And** `RoutingDecision.margin is None`
**And** `RetrieveMetadata.router_layer_used == "keyword"`
**And** `search.hybrid_search` ontvangt `kb_slugs=["mitel-helpcenter"]`

### AC-ROUTER-3.3 — Layer 2 semantic margin dual

**Given** een org met 6 KBs
**And** `query_vector` heeft cosine similarities `voys-help=0.72`, `voys-wiki=0.65`, `redcactus-wiki=0.55`, anderen `< 0.50`
**And** Layer 1 levert geen keyword-match
**And** `router_margin_single=0.15`, `router_margin_dual=0.08`
**When** `route_to_kbs` wordt aangeroepen
**Then** top1 (0.72) − top2 (0.65) = 0.07 < 0.08 → geen single-route
**And** de margin is wel ≥ 0.08 cumulatief voor top-2 boven rest → dual-route
**And** `RoutingDecision.selected_kbs` bevat `["voys-help", "voys-wiki"]` (of een analoog dual-paar volgens de geïmplementeerde regel)
**And** `RoutingDecision.layer_used == "semantic"`

### AC-ROUTER-3.4 — Layer 3 timeout fail-open

**Given** config `router_llm_fallback=True`
**And** Layer 1 geen match, Layer 2 margin < `router_margin_dual`
**And** de klai-fast LLM call throwt een `TimeoutError` na 500ms
**When** `route_to_kbs` wordt aangeroepen
**Then** `RoutingDecision.selected_kbs is None` (fail-open, geen filter)
**And** `RoutingDecision.layer_used == "llm"` met markering dat het een timeout was (via log)
**And** `search.hybrid_search` wordt aangeroepen ZONDER `kb_slugs` filter
**And** de retrieve-call faalt NIET

### AC-ROUTER-3.5 — Centroid cache hit

**Given** eerste request voor `org_id=X` met `router_enabled=True` heeft centroids opgebouwd en gecachet
**And** de cached entry is 5 minuten oud (< 600s TTL)
**When** een tweede request voor dezelfde `org_id` binnenkomt
**Then** `RetrieveMetadata.router_centroid_cache_hit == True`
**And** er wordt GEEN embedding call naar de embedder gedaan voor de centroids

## Shadow-mode across all three changes

### AC-SHADOW-1 — Shadow logs without applying

**Given** `source_quota_enabled=False` + `router_enabled=False` + `prefix_template_version=1`
**And** request-header `force_shadow=True`
**When** de retrieve-pipeline draait
**Then** `RetrieveMetadata` bevat zowel simulated quota-velden als simulated router-velden
**And** de daadwerkelijke pipeline-uitkomst is baseline (geen quota, geen router filter, oude prefix voor bestaande chunks)

## Performance gates

### AC-PERF-1 — Router overhead

**Given** een production-achtige retrieve-call met `router_enabled=True`, Layer 2 semantic margin path, centroid cache hit
**When** we p95-latency meten over 100 herhalingen
**Then** de router-overhead voegt ≤ 20ms p95 toe aan de totale retrieve-latency

### AC-PERF-2 — Layer 3 budget

**Given** `router_llm_fallback=True`
**When** Layer 3 wordt geraakt
**Then** de harde timeout is 500ms
**And** bij timeout is de volledige extra-latency ≤ 510ms (500ms timeout + ≤ 10ms overhead)

## Activation gate (HARD, per flag)

### AC-GATE-1 — Context Precision improvement

**Given** 50 Voys-representatieve queries in `klai-retrieval-api/evaluation/test_queries_curated.json`, hand-labeled met ground-truth kb_slug + chunk_ids
**And** `eval_runner.py` draait zowel baseline (flag off) als shadow (flag aan, via shadow-log replay)
**When** we de paired Wilcoxon signed-rank test uitvoeren op Context Precision
**Then** de flag wordt alleen geactiveerd als verbetering ≥ 5% met p < 0.05
**And** NDCG@10 vertoont géén regressie (shadow ≥ baseline binnen ruis-marge)

### AC-GATE-2 — Independent activation

**Given** meerdere flags passeren de gate
**When** we ze activeren
**Then** elke flag krijgt een eigen deploy-window
**And** nooit twee flags tegelijk in dezelfde deploy
