# SPEC-KB-021 — Implementation Plan

## Technology stack (confirm existing)

- Python 3.12
- FastAPI (knowledge-ingest, retrieval-api)
- httpx (LLM calls via klai-fast / klai-master)
- Qdrant client (vector storage + payload filtering)
- Pydantic v2 (IngestRequest, EnrichmentResult, ChunkResult, RetrieveMetadata)
- SQLAlchemy (klai-portal backend, PortalKnowledgeBase model)
- pytest + httpx.AsyncClient (existing test harness)

Geen nieuwe dependencies. Alle drie de veranderingen gebruiken alleen de huidige stack.

## Task decomposition

De drie veranderingen worden in de genoemde volgorde geïmplementeerd zodat de downstream componenten op upstream data kunnen leunen. Elk met eigen feature flag en onafhankelijk activeerbaar.

### Change 1 — Source-aware enrichment (ingestion-side)

**Priority: High**

Files:

- [`klai-knowledge-ingest/knowledge_ingest/enrichment.py`](klai-knowledge-ingest/knowledge_ingest/enrichment.py) — uitbreiden `ENRICHMENT_PROMPT` rond regel 22-41, uitbreiden `EnrichmentResult` met `content_type: Literal[...]`, bouwen deterministische fallback prefix template (`"{kb_name}: {title}"`) bij LLM-failure. @MX:TODO verify exact line numbers during /moai run.
- [`klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py`](klai-knowledge-ingest/knowledge_ingest/ingest_tasks.py) — thread `kb_slug`, `kb_name`, `connector_type`, `source_domain` via `extra_payload` naar `enrich_chunk`.
- [`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`](klai-knowledge-ingest/knowledge_ingest/routes/ingest.py) — nieuwe velden op `IngestRequest`: `kb_name: str`, `connector_type: str`, `source_domain: str | None = None`.
- [`klai-knowledge-ingest/knowledge_ingest/config.py`](klai-knowledge-ingest/knowledge_ingest/config.py) — `prefix_template_version: int = 2`.

Steps:

1. Voeg de nieuwe `IngestRequest` velden toe met backward-compatibele defaults (nullable of afgeleid uit bestaande velden waar mogelijk).
2. Update `ingest_tasks.extra_payload` om deze velden door te geven.
3. Herschrijf `ENRICHMENT_PROMPT` volgens het template in de brief (kb_name, connector_type, source, title, content_type_hint).
4. Breid `EnrichmentResult` uit met `content_type: Literal["procedural", "conceptual", "reference", "warning", "example"]`.
5. Schrijf `prefix_template_version` in de Qdrant payload bij elke nieuwe chunk.
6. Implementeer de deterministische fallback-prefix bij JSON-parse-failure of LLM-timeout — ingestion mag nooit blokkeren.
7. Unit tests voor: happy path, LLM-failure fallback, content_type enum validatie, token-length cap (≤ 120).

### Change 2 — Source quota post-rerank (retrieval-side)

**Priority: High**

Files:

- [`klai-retrieval-api/retrieval_api/services/diversity.py`](klai-retrieval-api/retrieval_api/services/diversity.py) — **NEW**, implementeer `source_quota_select(reranked, query_resolved, top_n=5, max_per_source=2) -> list[dict]`.
- [`klai-retrieval-api/retrieval_api/models.py`](klai-retrieval-api/retrieval_api/models.py) — voeg `kb_slug: str | None = None` toe aan `ChunkResult` (rond regel 21-41). Breid `RetrieveMetadata` uit met `quota_applied`, `quota_per_source_counts`, `quota_bypass_reason`.
- [`klai-retrieval-api/retrieval_api/api/retrieve.py`](klai-retrieval-api/retrieval_api/api/retrieve.py) — hook-in tussen reranker-output (rond regel 168) en `quality_boost` (rond regel 177). @MX:TODO verify exact line numbers during /moai run.
- [`klai-retrieval-api/retrieval_api/config.py`](klai-retrieval-api/retrieval_api/config.py) — `source_quota_enabled: bool = False`, `source_quota_max_per_source: int = 2`, `source_quota_bypass_on_mention: bool = True`.

Steps:

1. Garandeer dat `kb_slug` uit de Qdrant payload in de raw_results → reranked keten wordt mee-genomen. Voeg `kb_slug` toe aan `ChunkResult`.
2. Implementeer `source_quota_select`: greedy select op reranker_score desc, per-source counter, fallback op original score order bij onderbezetting.
3. Implementeer dynamic bypass: case-insensitive substring match van kb_slug (len > 3) in `query_resolved.lower()`.
4. Wire-volgorde: reranker → **source_quota (nieuw)** → quality_boost. Pattern-precedent: [`retrieval_api/quality_boost.py`](klai-retrieval-api/retrieval_api/services/quality_boost.py).
5. Implementeer shadow-mode: indien `enabled=False` maar `force_shadow=True`, compute quota decision, log in RetrieveMetadata, return unmodified reranked.
6. Unit tests volgens patroon van [`tests/test_quality_boost.py`](klai-retrieval-api/tests/test_quality_boost.py): happy path, bypass on mention, fallback when underfilled, shadow-mode.

### Change 3 — Three-layer query router (retrieval-side)

**Priority: Medium**

Files:

- [`klai-retrieval-api/retrieval_api/services/router.py`](klai-retrieval-api/retrieval_api/services/router.py) — **NEW**. Implementeer `async def route_to_kbs(query_resolved, query_vector, org_id, kb_catalog) -> RoutingDecision`.
- [`klai-retrieval-api/retrieval_api/api/retrieve.py`](klai-retrieval-api/retrieval_api/api/retrieve.py) — hook-in na coreference + gate, vóór `search.hybrid_search` (rond regel 63-94). Skip wanneer `req.kb_slugs is not None`.
- [`klai-retrieval-api/retrieval_api/models.py`](klai-retrieval-api/retrieval_api/models.py) — `RoutingDecision` pydantic model (`selected_kbs: list[str] | None`, `layer_used: str`, `margin: float | None`, `cache_hit: bool`). Breid `RetrieveMetadata` uit met `router_decision`, `router_layer_used`, `router_margin`, `router_centroid_cache_hit`.
- [`klai-retrieval-api/retrieval_api/config.py`](klai-retrieval-api/retrieval_api/config.py) — `router_enabled: bool = False`, `router_min_kb_count: int = 4`, `router_margin_single: float = 0.15`, `router_margin_dual: float = 0.08`, `router_llm_fallback: bool = False`, `router_centroid_ttl_seconds: int = 600`.
- [`klai-portal/backend/app/models/knowledge_bases.py`](klai-portal/backend/app/models/knowledge_bases.py) — verifieer `PortalKnowledgeBase.description` (rond regel 22). @MX:TODO verify during /moai run.
- Frontend KB-creatie formulier — minimum lengte 10 chars voor nieuwe KBs; bestaande KBs houden `null`.

Steps:

1. Implementeer Layer 1 (keyword gate): bouw `{brand_term → kb_slug}` map uit `(kb_name + kb_description).lower()` gesplitst op whitespace + tokens. Exact lowercase substring match. <1ms.
2. Implementeer Layer 2 (semantic margin): centroid per KB via `embed("{name}. {description}")`. Cosine similarity vs `query_vector`. Margin-regels: `top1-top2 > margin_single` → single, `> margin_dual` → dual, anders no-filter.
3. Implementeer centroid cache: in-memory dict keyed op `org_id`, TTL 600s, pattern precedent: [`retrieval_api/services/gate.py`](klai-retrieval-api/retrieval_api/services/gate.py).
4. Implementeer Layer 3 (LLM fallback): klai-fast call met 500ms timeout, pattern precedent: [`retrieval_api/services/coreference.py`](klai-retrieval-api/retrieval_api/services/coreference.py). Fail-open bij timeout/error.
5. Trigger-conditie in `retrieve.py`: `req.kb_slugs is None` AND `org.kb_count >= router_min_kb_count` AND `req.scope in {"org", "both"}` AND `config.router_enabled`.
6. **User-override enforcement:** als `req.kb_slugs is not None`, skip router volledig (geen log, geen latency, geen `layer_used="skipped"` — gewoon niet aanroepen).
7. Verifieer / enforce in portal frontend: `description` minimum 10 chars voor nieuwe KBs.
8. Unit tests volgens patronen van [`tests/test_gate.py`](klai-retrieval-api/tests/test_gate.py) en [`tests/test_coreference.py`](klai-retrieval-api/tests/test_coreference.py): user-override skip, Layer 1 keyword hit, Layer 2 margin single/dual/none, Layer 3 timeout fail-open, cache hit/miss.

## Dependency map

```
SPEC-KB-008 (retrieval-api split)
    └─> SPEC-KB-021 (deze SPEC) — foundation aanwezig, geen conflict

SPEC-KB-014 (gap detection)
    <─> SPEC-KB-021 — router kan gap-rate beïnvloeden; meten op eval set

SPEC-KB-015 (self-learning quality_boost)
    <─> SPEC-KB-021 — quota draait VÓÓR quality_boost in de pipeline;
                       wire-volgorde: reranker → source_quota → quality_boost

SPEC-KB-022 (Taxonomy V2 multi-label tagging)
    <─> SPEC-KB-021 — beide raken enrichment.py aan;
                       content_type (ons) en taxonomy tags (KB-022) zijn
                       aparte velden in EnrichmentResult, geen naamconflict

SPEC-KB-023 (Taxonomy Discovery blind labeling)
    <─> SPEC-KB-021 — beide raken enrichment.py aan;
                       design for coexistence in EnrichmentResult

SPEC-KB-024 (Taxonomy Discovery embedding clustering)
    <─> SPEC-KB-021 — draait na enrichment, geen conflict

SPEC-CRAWLER-003 (authority boost)
    <─> SPEC-KB-021 — authority is PRE-rerank, quota is POST-rerank;
                       composen cleanly

SPEC-EVIDENCE-001 (RAGAS eval framework)
    └─> SPEC-KB-021 — REUSE: eval_runner.py + 50 nieuwe Voys queries
                       als activation gate
```

## Risk analysis

### Risk 1 — Prefix re-embed cost

Als we besluiten alle bestaande chunks opnieuw te embedden met de nieuwe prompt, is dat een zware bulk-operatie voor grote orgs. **Likelihood: Medium** (alleen als eval slecht presteert op oude chunks), **Impact: High** (dagen werk + LLM-kosten).

### Risk 2 — Centroid staleness

KB-description updates in portal triggeren geen centroid invalidatie (niet in MVP). Gebruikers kunnen description wijzigen terwijl router nog oude centroid gebruikt. **Likelihood: Medium**, **Impact: Low-Medium** (routing decision tijdelijk suboptimaal).

### Risk 3 — Cold-start voor keyword map

Eerste query op een warm-geboot-process betaalt de keyword-map bouw. **Likelihood: High** (bij elke restart), **Impact: Low** (eenmalig per org per proces).

### Risk 4 — Latency impact Layer 3 LLM

Een 500ms timeout toegevoegd aan queries die Layer 1+2 niet oplossen. **Likelihood: Medium** (afhankelijk van query-diversiteit), **Impact: Medium** (p95 latency kan 300-500ms omhoog voor die subset).

### Risk 5 — Quota verkleint de nuttige recall

Bij een bron-specifieke query die per ongeluk geen exact kb_slug-vermelding bevat, dwingt quota tot diversificatie die de werkelijk-beste chunks verdringt. **Likelihood: Medium**, **Impact: Medium**.

### Risk 6 — EnrichmentResult veldconflict met SPEC-KB-022/023

Drie SPECs schrijven in dezelfde pydantic model. **Likelihood: High**, **Impact: Medium** (merge conflict, veld naming clash).

### Risk 7 — `quality_boost` semantiek verandert door quota

Quota wijzigt de input-volgorde van `quality_boost`. Als `quality_boost` impliciet afhangt van reranker-score-distributie, kunnen we effectiviteit verliezen. **Likelihood: Low-Medium**, **Impact: Medium**.

## Mitigation strategies

- **Risk 1 (re-embed cost):** Niet forceren. `prefix_template_version: int` in de Qdrant payload. Selectief her-embedden per KB indien eval op oude chunks slecht presteert. Nieuwe chunks gebruiken automatisch v2.
- **Risk 2 (centroid staleness):** 10-minuten TTL als ondergrens. Invalidatie-webhook op KB update is nice-to-have follow-up, niet MVP.
- **Risk 3 (cold-start):** Eager build van keyword map + centroids bij eerste request per org, gecachet zolang proces leeft. Geen pre-warm nodig.
- **Risk 4 (Layer 3 latency):** Layer 3 default `False` bij launch. Alleen activeren na shadow-analyse die aantoont dat Layer 1+2 > 10% van queries onbeslist laat. Harde 500ms timeout, fail-open.
- **Risk 5 (quota verkleint recall):** Dynamic bypass op kb_slug-mention is de eerste mitigatie. Activation gate meet dit via de eval set. Roll-back via flag flip indien Context Precision daalt.
- **Risk 6 (EnrichmentResult conflict):** Coördineer met SPEC-KB-022/023 auteurs: één gedeelde velden-lijst, alfabetische volgorde in model, ieder SPEC eigen niet-overlappende velden. Merge-PR volgorde: eerst KB-022, dan KB-023, dan KB-021 — of synchroon in één PR.
- **Risk 7 (quality_boost semantiek):** Activation gate meet `quality_boost effectiveness` apart. Indien effectiviteit daalt, overweeg quota ná quality_boost te zetten of quality_boost-parameters te herstellen. Shadow-mode logt beide volgordes.

## mx_plan

### @MX:ANCHOR candidates

- **`source_quota_select`** in `klai-retrieval-api/retrieval_api/services/diversity.py` — wordt aangeroepen vanuit `retrieve.py`, gedrag is een invariant-contract (ordering + quota enforcement). Fan_in zal ≥ 2 worden (retrieve + tests). **Annotate as `@MX:ANCHOR`** met expliciet ordering-contract in de docstring.
- **`route_to_kbs`** in `klai-retrieval-api/retrieval_api/services/router.py` — centraal beslispunt voor de scope van de query. Fan_in groeit (retrieve + tests + mogelijk toekomstige caller). **Annotate as `@MX:ANCHOR`**.
- **`enrich_chunk`** in `klai-knowledge-ingest/knowledge_ingest/enrichment.py` — fan_in ≥ 3 (ingest_tasks, tests, re-embed scripts). **Annotate as `@MX:ANCHOR`** met `content_type` enum contract.

### @MX:WARN candidates

- **Layer 3 LLM fallback** in `route_to_kbs` — time-bound external call met fail-open behavior. **Annotate as `@MX:WARN`** met `@MX:REASON: 500ms timeout, LLM availability beïnvloedt routing-latency`.
- **Centroid cache-refresh** in `router.py` — in-memory TTL cache zonder cross-process sync. **Annotate as `@MX:WARN`** met `@MX:REASON: multi-worker processes kunnen divergente centroids serveren, acceptabel binnen 10min TTL`.

### @MX:NOTE candidates

- **Source-aware prefix generation** in `enrich_chunk` — nieuwe gedragsmode, template v2. **Annotate as `@MX:NOTE`** met verwijzing naar SPEC-KB-021 en `prefix_template_version` contract.
- **Dynamic bypass logic** in `source_quota_select` — niet-triviaal, voorkomt dat expliciete bron-vermelding tot diversificatie leidt. **Annotate as `@MX:NOTE`** met voorbeeld en reden.

### @MX:TODO markers

- `@MX:TODO verify exact line numbers during /moai run` op alle regelnummer-referenties in deze SPEC (168, 177, 63-94, 22-41, 22).
- `@MX:TODO coordinate field layout with SPEC-KB-022 and SPEC-KB-023 before merging EnrichmentResult changes`.

## Reference implementations in codebase

- **Post-rerank step pattern:** [`klai-retrieval-api/retrieval_api/services/quality_boost.py`](klai-retrieval-api/retrieval_api/services/quality_boost.py) — dezelfde signatuurvorm als wat `source_quota_select` krijgt.
- **Centroid cache + margin check pattern:** [`klai-retrieval-api/retrieval_api/services/gate.py`](klai-retrieval-api/retrieval_api/services/gate.py) — `_ensure_reference_vectors` laat zien hoe je een in-memory cache met TTL bouwt rond embedding-computaties.
- **klai-fast LLM call pattern:** [`klai-retrieval-api/retrieval_api/services/coreference.py`](klai-retrieval-api/retrieval_api/services/coreference.py) — `_call_llm` met httpx-client, timeout, JSON-parse, fail-open gedrag.
- **Eval framework:** [`klai-retrieval-api/evaluation/eval_runner.py`](klai-retrieval-api/evaluation/eval_runner.py) en [`klai-retrieval-api/evaluation/test_queries_curated.json`](klai-retrieval-api/evaluation/test_queries_curated.json) — REUSE voor activation gate.
- **Test patterns:** [`klai-retrieval-api/tests/test_quality_boost.py`](klai-retrieval-api/tests/test_quality_boost.py), [`klai-retrieval-api/tests/test_gate.py`](klai-retrieval-api/tests/test_gate.py), [`klai-retrieval-api/tests/test_coreference.py`](klai-retrieval-api/tests/test_coreference.py).

## Rollout

1. **Deploy-stap 1:** Alle drie de flags off. Code-paden inactief. Veilige no-op deploy.
2. **Shadow-stap 2 (per flag):** Activeer shadow-logging door `force_shadow=True` te accepteren op de request. Decisions worden gelogd in `RetrieveMetadata` maar niet toegepast.
3. **Meet-stap 3 (per flag):** Minimaal 1 week Voys-trafiek, verzamel shadow-logs.
4. **Eval-stap 4 (per flag):** Run `eval_runner.py` op 50 Voys-queries baseline (flag off) vs shadow (flag gesimuleerd via shadow-log). Wilcoxon signed-rank paired test. Activeer alleen bij ≥ 5% Context Precision verbetering (p < 0.05) én non-regression op NDCG@10.
5. **Activeer-stap 5 (per flag):** Flip de flag in productie. Eén flag per deploy-window.
6. **Monitor-stap 6:** Dashboard op Context Precision, NDCG@10, Faithfulness, quality_boost effectiveness. Regressie op één van deze = rollback trigger.
