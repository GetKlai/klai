# SPEC-KB-008: De Retrieval API

> Status: COMPLETED (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-005 (contextual retrieval), SPEC-KB-006 (content-type adapters), SPEC-KB-007 (sparse vectors + hybrid search)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md`
> Created: 2026-03-26

---

## Wat bestaat er vandaag

Retrieval vindt nu op twee plaatsen plaats, zonder centrale service:

1. **`research-api`** (Focus notebooks): `app/services/retrieval.py` embedt de query via TEI, zoekt in Qdrant collection `klai_focus` met verplichte tenant_id + notebook_id filters, en streamt een LLM-antwoord via SSE. Voor "broad" mode roept het `knowledge_client.py` aan die direct de `/knowledge/v1/retrieve` endpoint van `knowledge-ingest` aanroept.

2. **`knowledge-ingest`**: exposeert `/knowledge/v1/retrieve` als intern endpoint. Implementeert directe Qdrant-query op `klai_knowledge` collection, geen reranking, geen pre-retrieval gate, geen coreferentie-resolutie.

Focus en de kennislaag zijn architecturaal identiek: zelfde BGE-M3 embedder, zelfde Qdrant-patroon, zelfde hybrid search (na KB-007). Het enige verschil is de collection en de scope-filter. Focus-documenten leven in `klai_focus` (per-notebook), KB-documenten in `klai_knowledge` (per-org, met personal/org scope). De retrieval-pipeline is in beide gevallen hetzelfde.

Na KB-005/006/007 heeft elke chunk in Qdrant collection `klai_knowledge`:
- named vector `dense` (BGE-M3 dense, embedding van `context_prefix + chunk_text`)
- named vector `sparse` (BGE-M3 sparse)
- named vector `questions` (HyPE, alleen voor synthesis_depth 0-1)
- payload: `chunk_text`, `context_prefix`, `content_type`, `synthesis_depth`, `artifact_id`, `org_id`, `scope` (personal/org), `created_at`, `valid_at`, `invalid_at`

`klai_focus` gebruikt dezelfde vectorstructuur, met `notebook_id` en `tenant_id` als scope-velden in de payload.

Er is geen cross-encoder reranking. Er is geen pre-retrieval gate. Multi-turn queries zoals "Wat zei hij daarin over het budget?" werken niet. Focus en KB delen deze tekortkomingen — verbeteringen moeten in beide gaan werken.

---

## Wat deze SPEC bouwt

Een nieuwe centrale `retrieval-api` service in `klai-mono/klai-retrieval-api/` met twee endpoints:

1. **`POST /retrieve`** — machine-leesbare interface voor AI-agents en MCP clients. Geeft een JSON array van chunks terug. Geen synthese. Lage latency-budget.

2. **`POST /chat`** — menselijke interface. Geeft een gesynthetiseerd antwoord terug met inline citaties. Hogere latency acceptabel.

Beide endpoints doorlopen dezelfde pipeline:
```
coreference resolutie → pre-retrieval gate → hybrid search (KB-007) → cross-encoder reranking
```

`/chat` voegt daarna LLM-synthese toe. `/retrieve` stopt na reranking.

Na deze SPEC:
- `research-api` `retrieval.py` delegeert **alle** retrieval (narrow, broad, én Focus-eigen documenten) aan retrieval-api. research-api behoudt alleen de Focus-specifieke lagen: notebook CRUD, source management, ingest, chat history, SSE streaming.
- `knowledge-ingest` behoudt `/knowledge/v1/retrieve` tijdelijk voor backwards compatibility (deprecated)

---

## API contract

### POST /retrieve

**Request:**
```json
{
  "query": "Wat is ons retourbeleid voor B2B-klanten?",
  "org_id": "org_abc123",
  "scope": "org",
  "user_id": "user_xyz",
  "top_k": 8,
  "conversation_history": [
    {"role": "user", "content": "We hadden een gesprek over factuurproblemen."},
    {"role": "assistant", "content": "Ja, die problemen zijn gerelateerd aan module Q2."}
  ]
}
```

| Veld | Type | Verplicht | Default | Toelichting |
|---|---|---|---|---|
| `query` | string | ja | — | Ruwe query van de gebruiker of agent |
| `org_id` | string | ja | — | Tenant identifier |
| `scope` | enum | nee | `"org"` | `"personal"`, `"org"`, `"both"`, of `"notebook"` |
| `user_id` | string | conditioneel | — | Verplicht wanneer `scope = "personal"` of `"both"` |
| `notebook_id` | string | conditioneel | — | Verplicht wanneer `scope = "notebook"`. Zoekt in `klai_focus` collection, gefilterd op dit notebook. |
| `top_k` | integer | nee | 8 | Aantal chunks in de response (na reranking) |
| `conversation_history` | array | nee | `[]` | Laatste N beurten voor coreferentie-resolutie. Max 10 beurten, alleen de laatste 3 worden gebruikt. |

**Response:**
```json
{
  "query_resolved": "Wat is ons retourbeleid voor B2B-klanten?",
  "retrieval_bypassed": false,
  "chunks": [
    {
      "chunk_id": "c_7f3a...",
      "artifact_id": "a_1b2c...",
      "content_type": "kb_article",
      "text": "Het retourbeleid voor B2B-klanten geldt tot 30 dagen na levering.",
      "context_prefix": "Dit chunk is afkomstig uit het document 'Retourbeleid', sectie B2B.",
      "score": 0.87,
      "reranker_score": 0.93,
      "scope": "org",
      "valid_at": "2025-01-01T00:00:00Z",
      "invalid_at": null
    }
  ],
  "metadata": {
    "candidates_retrieved": 60,
    "reranked_to": 8,
    "retrieval_ms": 42,
    "rerank_ms": 315,
    "gate_margin": 0.31
  }
}
```

| Veld | Type | Toelichting |
|---|---|---|
| `query_resolved` | string | Query na coreferentie-resolutie. Gelijk aan `query` als geen history meegegeven of resolutie geen wijziging maakte. |
| `retrieval_bypassed` | boolean | `true` wanneer de pre-retrieval gate besloot dat retrieval niet helpt. `chunks` is dan leeg. |
| `chunks[].text` | string | Originele chunk tekst (zonder context_prefix). |
| `chunks[].score` | float | RRF-fused Qdrant score. |
| `chunks[].reranker_score` | float | Cross-encoder score. Null wanneer reranking uitgeschakeld is. |
| `metadata.gate_margin` | float | TARG margin score. Monitoring-veld. Null wanneer pre-retrieval gate uitgeschakeld is. |

### POST /chat

**Request:** identiek aan `/retrieve`.

**Response:**
```json
{
  "answer": "Het retourbeleid voor B2B-klanten staat toe dat producten tot 30 dagen na levering worden teruggestuurd, mits de originele verpakking intact is [1].",
  "citations": [
    {
      "index": 1,
      "artifact_id": "a_1b2c...",
      "title": "Retourbeleid — B2B sectie",
      "chunk_ids": ["c_7f3a..."],
      "relevance_score": 0.93
    }
  ],
  "retrieval_bypassed": false,
  "query_resolved": "Wat is ons retourbeleid voor B2B-klanten?"
}
```

Citaties zijn genummerd en komen overeen met `[n]` verwijzingen in `answer`. Wanneer `retrieval_bypassed = true` is `answer` een direct LLM-antwoord zonder KB-context en is `citations` leeg.

### Errors

| HTTP code | Situatie |
|---|---|
| 400 | `scope = "personal"` of `"both"` zonder `user_id` |
| 400 | `scope = "notebook"` zonder `notebook_id` |
| 422 | Ongeldige request body (Pydantic validatie) |
| 503 | TEI of Qdrant niet bereikbaar |

---

## Pipeline architectuur

### Stap 1: Coreferentie-resolutie

**Wanneer:** `conversation_history` is niet leeg.

**Wat:** Vervang onduidelijke verwijzingen in de query voor de zoekstap. "Wat zei hij daarin over het budget?" wordt "Wat zei Jan de Vries in het vergaderverslag van 10 maart over het budget?"

**Hoe:**
- LLM-call met `klai-fast`
- Input: laatste 3 beurten uit history + huidige query
- Output: standalone query (string)
- Timeout: 3 seconden. Bij timeout: gebruik originele query ongewijzigd.
- Geen LLM-call wanneer geen history aanwezig is.

**Waarom vóór alles:** coreferentie moet worden opgelost voordat de query wordt geëmbed. Een query embedden die naar "hij" verwijst geeft een vector die niet matcht met de relevante chunks.

### Stap 2: Pre-retrieval gate (TARG margin gate)

**Wanneer:** altijd, tenzij `RETRIEVAL_GATE_ENABLED=false`.

**Wat:** Beslis of retrieval de query-kwaliteit verbetert. 31.46% van queries is net-negatief (RAGRouter benchmark, 2025) — het model geeft een beter antwoord zonder KB-context.

**Hoe (TARG margin gate, arXiv:2511.09803):**
1. Embed de resolved query via TEI (deze embedding wordt ook hergebruikt in stap 3).
2. Vergelijk de query-embedding met een referentieset van "zelf-voldoende queries" (factual QA queries zonder context nodig): cosine similarity voor de top-1 en top-2 match.
3. Margin = `similarity_top1 - similarity_top2`.
4. Als `margin > RETRIEVAL_GATE_THRESHOLD` (default: 0.1): sla retrieval over. Geef lege chunks terug met `retrieval_bypassed = true`.

**Initiële drempel:** 0.1 — zeer conservatief. Bij drempel 0.1 wordt alleen retrieval overgeslagen wanneer de query sterk lijkt op bekende factual queries die geen context nodig hebben (rekensommen, definitievragen over algemene kennis). Zie D3 voor kalibratie-aanpak.

### Stap 3: Hybrid search

Hergebruik de query-embedding uit stap 2 (geen herberekening).

Qdrant prefetch met RRF fusion (zoals gedefinieerd in KB-007):
```python
results = client.query_points(
    collection_name="klai_knowledge",
    prefetch=[
        models.Prefetch(query=dense_vector,   using="dense",     limit=candidates),
        models.Prefetch(query=sparse_vector,  using="sparse",    limit=candidates),
        models.Prefetch(query=dense_vector,   using="questions", limit=candidates),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    filter=scope_filter,
    limit=candidates,  # 60 standaard (zie D2)
)
```

Scope bepaalt zowel de collection als de filter:

| scope | Collection | Qdrant filter |
|---|---|---|
| `"org"` | `klai_knowledge` | `org_id = X AND scope = "org"` |
| `"personal"` | `klai_knowledge` | `org_id = X AND user_id = Y AND scope = "personal"` |
| `"both"` | `klai_knowledge` | `org_id = X AND (scope = "org" OR (scope = "personal" AND user_id = Y))` |
| `"notebook"` | `klai_focus` | `tenant_id = X AND notebook_id = Z` |

Voor `scope = "broad"` (research-api broad mode na migratie): retrieval-api voert twee parallelle queries uit — één op `klai_focus` (notebook) en één op `klai_knowledge` (org) — en voegt de resultaten samen via normalized score merge vóór reranking. Dit is het equivalent van de huidige `retrieve_broad_chunks` logica in research-api, gecentraliseerd.

### Stap 4: Cross-encoder reranking

Stuur de top-N kandidaten (default: 60) naar de reranker:

```python
reranker_results = await reranker.rerank(
    query=query_resolved,
    passages=[chunk.text for chunk in candidates],
    top_k=top_k,
)
```

Reranker geeft scores terug per passage. Kandidaten worden gesorteerd op reranker_score, top-`top_k` worden teruggegeven.

Bij reranker-uitval (timeout of HTTP-fout): geef Qdrant-resultaten direct terug (zonder reranking), log een warning, stel `reranker_score = null` in de response.

### Stap 5a: /retrieve response

Geef de gererankte chunks terug als JSON array. Klaar.

### Stap 5b: /chat synthese

LLM-call met `klai-primary`:
- System prompt: bevat KB-context (chunks samengevoegd, max 6000 tokens, met bronvermelding per chunk)
- User message: `query_resolved`
- History: `conversation_history` als chat messages
- Instructie: antwoord in dezelfde taal als de query, verwijs naar bronnen als `[n]`
- Streaming: nee (`/chat` geeft een volledige response terug, geen SSE)

Citaties worden geëxtraheerd uit welke chunks in de synthese zijn gebruikt.

---

## Design decisions

### D1: Nieuwe service vs uitbreiding van `research-api`

**Gekozen: nieuwe service (`klai-mono/klai-retrieval-api/`).**

Focus en de kennislaag zijn technisch identiek: zelfde embedder, zelfde Qdrant-patroon, zelfde hybrid search pipeline. Focus is de kennislaag maar dan smal — persoonlijke, projectgebonden documenten in plaats van organisatiegeheugen. Focus is ook de upsell: gebruikers ervaren in Focus hoe krachtig de retrieval-laag is, en stappen daarna over naar de kennislaag.

Dit heeft een directe architectuurconsequentie: **de retrieval-pipeline mag maar op één plek bestaan.** Als Focus en KB elk hun eigen retrieval-implementatie hebben, groeien ze uit elkaar. Reranking toevoegen voor KB maar niet Focus, of vice versa, verslechtert de upsell-ervaring en verdubbelt de onderhoudslast.

**Waarom dan niet uitbreiden in research-api?**

research-api is gebouwd rondom de Focus UX-laag: notebook CRUD, source management, ingest-workflows, chat history, SSE streaming. Dat is terecht, en dat blijft zo. Maar de retrieval-logica daarin is een implementatiedetail dat de service onnodig compliceert als het ook KB-queries moet afhandelen.

Een nieuwe `retrieval-api` service bevat alleen retrieval — geen notebooks, geen sources, geen history. research-api wordt een dunne delegerende laag: het beheert de Focus UX en roept retrieval-api aan voor elke zoekactie.

| | Nieuwe service | Uitbreiding research-api |
|---|---|---|
| Pipeline coherentie | Focus en KB gebruiken exact dezelfde pipeline | Pipeline-drift zodra één van de twee afwijkt |
| research-api complexiteit | research-api blijft gefocust op Focus UX | research-api groeit met KB-scope-logica die er niet thuishoort |
| Deployability | Onafhankelijk schaalbaar | Retrieval-last trekt Focus-service mee |
| Migratie-overhead | research-api `retrieval.py` wordt een dunne client | Minder werk bij eerste implementatie, meer drift-risico daarna |

Gedeelde infrastructuur (TEI, Qdrant, LiteLLM) wordt via configuratie aangeroepen, niet via shared Python code.

### D2: Reranking — altijd, nooit, of conditioneel?

**Gekozen: conditioneel op scope.**

| Scope | Reranking | Rationale |
|---|---|---|
| `notebook` | Uit | Focus is snel en persoonlijk — snelheid prevaleert over precisie |
| `org`, `personal`, `both`, `broad` | Aan | KB-queries zijn grondig van aard; +200-500ms is acceptabel |

Geen intent-classificatie nodig: de scope zelf is de beslissende factor. Focus en KB hebben een fundamenteel ander karakter — dat is al bekend bij de request.

**Kandidatenpool:** 60 (aanbevolen sweet spot: 50-75 uit het onderzoek). Met top_k=8 betekent dit een reranking-ratio van 7.5x.

**Model:** `BAAI/bge-reranker-v2-m3`.

- Meertalig (Nederlands + Engels beide ondersteund — relevant voor internationalisering)
- Natuurlijke partner voor BGE-M3 embedder (zelfde family)
- Open-source, zelf-gehost via een tweede TEI-instantie met reranker-model
- TEI ondersteunt `/rerank` endpoint natively

**Deployment:** aparte TEI-instantie (`tei-reranker`) met model `BAAI/bge-reranker-v2-m3`. Niet gecombineerd met de embedding-TEI omdat modellen niet tegelijk geladen kunnen worden in dezelfde TEI-instantie. Als core-01 vol raakt, is `tei-reranker` een goede kandidaat om naar core-02 te verplaatsen.

### D3: Pre-retrieval gate — referentieset opbouwen zonder bestaande data

**Gekozen: synthetische generatie bij deployment, gevolgd door productie-kalibratie.**

Het probleem: TARG kalibratie vereist een referentieset van queries waarbij je weet of retrieval helpt of niet. Die data is er niet bij lancering.

**Fase 1 — Synthetische referentieset genereren:**

Een script (`scripts/generate_gate_reference.py`) roept `klai-fast` aan bij deployment en genereert 200 queries in twee categorieën:

- **Categorie A — geen retrieval nodig (100 queries):** wiskunde, logica, grammatica, algemene wereldkennis, taalvragen, creatief schrijven. Talen: 50% NL, 50% EN (in lijn met de internationale roadmap).
- **Categorie B — retrieval nodig (100 queries):** domeinspecifieke lookups, beleidsvragen, vragen met eigennamen of product-specifieke termen.

Output: `retrieval_api/data/gate_reference.jsonl`. Alleen categorie A wordt als referentieset gebruikt voor de margin gate.

Het script is idempotent: als `gate_reference.jsonl` al bestaat, doet het niets. Zo kun je de set handmatig aanvullen zonder dat een redeploy het overschrijft.

**Fase 2 — Conservatieve lancering (threshold=0.1):**
Bij margin 0.1 wordt retrieval zelden overgeslagen. Elke query logt `gate_margin` in structured logging.

**Fase 3 — Kalibratie op productiedata (na 4-6 weken):**
Analyseer de margin-verdeling. Queries met consistente hoge margin en goede antwoordkwaliteit (LLM-als-judge of gebruikersfeedback) worden toegevoegd aan de referentieset. Drempel bijstellen op basis van empirische verdeling (verwacht eindresultaat: 0.2-0.4).

### D4: Coreferentie-resolutie — LLM vs rule-based, meertalig

**Gekozen: LLM (klai-fast) zonder taalspecifieke heuristiek.**

Het systeem start in het Nederlands maar wordt internationaal — ook Engels zal ondersteund worden. Een hardcoded lijst van Nederlandse pronomina is dus een tijdbom: het werkt voor NL maar mist Engelse anaphorische uitdrukkingen ("what did he say in that report?") volledig.

**Aanpak:** verwijder de taalspecifieke heuristiek. Roep altijd de LLM aan wanneer `conversation_history` niet leeg is. Als de query geen coreferentie bevat, geeft het model de originele query ongewijzigd terug — dat is een snelle, goedkope call. De 3-seconden timeout is de enige guard.

Bij timeout of LLM-fout wordt de originele query gebruikt. Geen functionele regressie.

**Wanneer geen LLM-call:**
- `conversation_history` leeg of `null`
- Eerste beurt (geen vorige context)

**Taal-agnostisch by design:** de LLM-prompt instrueert het model om de resolutie uit te voeren in dezelfde taal als de query. Geen taaldetectie nodig.

> **Toekomstige overweging (niet in scope KB-008):** als coreferentie-calls een significante latency-bijdrage worden (meetbaar in productielogs), kan een lichte classifier worden getraind op query-lengte + turn-nummer als proxy. Tot die tijd is de LLM-aanpak de meest onderhoudsvriendelijke.

### D5: /chat — streaming via SSE

**Gekozen: SSE streaming voor /chat.**

Streaming beïnvloedt het eindresultaat niet: de LLM genereert dezelfde tokens, ze worden alleen geflushed zodra ze beschikbaar zijn in plaats van gebufferd. Retrieval en reranking lopen synchroon vóórdat de stream begint — de gebruiker ziet geen tokens totdat de KB-context klaar is.

**Flow:**
1. Retrieval + reranking (synchroon, ~200-800ms)
2. Stream openen: `Content-Type: text/event-stream`
3. LLM-synthese streamt tokens als `{ "type": "token", "content": "..." }`
4. Sluit af met `{ "type": "done", "citations": [...], "retrieval_bypassed": false }`

Dit is consistent met research-api's SSE-interface voor Focus — beide endpoints streamen op dezelfde manier.

**Logging:** volledige `answer` string wordt na afloop gelogd (geconstrueerd uit de gestreamde tokens). Geen impact op observability.

### D6: Validatie van `valid_at` / `invalid_at`

Chunks met `invalid_at` in het verleden worden standaard uitgefilterd in de Qdrant-query:

```python
filter = models.Filter(
    must=[
        scope_filter,
        models.FieldCondition(
            key="invalid_at",
            match=models.MatchAny(any=[None])  # NULL = nog steeds geldig
        ) | models.FieldCondition(
            key="invalid_at",
            range=models.DatetimeRange(gt=datetime.utcnow().isoformat())
        )
    ]
)
```

Dit filter is altijd actief. Er is geen opt-out in de API (vervallen content nooit tonen aan gebruikers).

---

## Changes aan bestaande services

### `research-api`: volledige retrieval-delegatie

`retrieval.py` wordt refactored tot een dunne client die retrieval-api aanroept voor alle drie de retrieval-modes:

| Huidige mode | Huidige implementatie | Na KB-008 |
|---|---|---|
| `narrow` | Directe Qdrant-query op `klai_focus` | `POST /retrieve` met `scope = "notebook"` |
| `broad` | Parallel: Qdrant + `knowledge_client.py` → knowledge-ingest | `POST /retrieve` met `scope = "broad"` + `notebook_id` |
| `web` | SearXNG + docling + cosine re-rank | Ongewijzigd — web retrieval blijft in research-api |

`knowledge_client.py` vervalt — de functionaliteit zit nu in retrieval-api.

research-api behoudt:
- Notebook CRUD, source management, ingest-workflows
- Chat history (PostgreSQL)
- SSE streaming (de `/chat` endpoint in research-api streamt; retrieval-api's `/chat` doet dat niet)
- Web retrieval (SearXNG + docling)

**Migratie-aanpak:** Vervang `retrieve_chunks` en `retrieve_broad_chunks` in `retrieval.py` door `retrieval_client.retrieve(...)`. De SSE-streaming in `chat.py` blijft ongewijzigd — die ontvangt nu chunks van retrieval-api in plaats van direct van Qdrant.

### `knowledge-ingest`: `/knowledge/v1/retrieve`

Behoudt het endpoint maar markeert het als deprecated in de OpenAPI docs. Wordt verwijderd in KB-010 of later.

---

## Nieuwe service: `retrieval-api`

**Locatie:** `klai-mono/klai-retrieval-api/`

**Port:** 8040

**Stack:** Python 3.12+, FastAPI, async/await, httpx, Pydantic v2

**Externe afhankelijkheden:**
- TEI (embedding): `http://tei:8080` — embed endpoint
- TEI-reranker: `http://tei-reranker:8080` — rerank endpoint (nieuw)
- Qdrant: `klai_knowledge` collection
- LiteLLM proxy: coreferentie-resolutie (`klai-fast`) + /chat synthese (`klai-primary`)

**Modules:**
```
retrieval_api/
├── main.py              # FastAPI app, lifespan
├── config.py            # Settings (env vars)
├── api/
│   ├── retrieve.py      # POST /retrieve
│   └── chat.py          # POST /chat
├── services/
│   ├── coreference.py   # Stap 1: LLM coreferentie-resolutie
│   ├── gate.py          # Stap 2: TARG pre-retrieval gate
│   ├── search.py        # Stap 3: Qdrant hybrid search
│   ├── reranker.py      # Stap 4: cross-encoder reranking
│   ├── synthesis.py     # Stap 5b: LLM synthese (/chat)
│   └── tei.py           # TEI embedding client (gekopieerd patroon van research-api)
└── models.py            # Pydantic request/response models
```

**Configuratie:**
```
QDRANT_URL
QDRANT_COLLECTION=klai_knowledge
TEI_URL=http://tei:8080
TEI_RERANKER_URL=http://tei-reranker:8080
LITELLM_URL=http://litellm:4000
LITELLM_API_KEY
RETRIEVAL_GATE_ENABLED=true
RETRIEVAL_GATE_THRESHOLD=0.1
RETRIEVAL_CANDIDATES=60
COREFERENCE_TIMEOUT=3.0
RERANKER_TIMEOUT=2.0
```

---

## Wat NIET in scope is

| Item | Waarom niet nu |
|---|---|
| Intent-classificatie en query-routing | Vereist gelabelde data en een getrainde classifier. Empirisch bewijs voor routing-accuraatheid ontbreekt (RAGRouter-Bench TPR 30.5%). Dit is KB-009. |
| GraphDB / Graphiti integratie | Kennisgraaf is nog niet gebouwd. Retrieval-api gebruikt alleen Qdrant. Graaf-retrieval is een aparte SPEC. |
| Streaming `/chat` | Volledige response is voldoende voor huidige use cases. SSE toevoegen is een losstaande uitbreiding. |
| MCP server wrapper | Retrieval-api exposeert een HTTP API. Een MCP wrapper die `/retrieve` aanroept is een aparte integratie (klai-mcp of portal-mcp). |
| Feedback loop (was retrieval nuttig?) | Monitoring van retrieval-kwaliteit via gebruikersfeedback is out of scope. Logs geven wel `gate_margin` en latency. |
| Caching van query-resultaten | Geen query-caching in de eerste versie. Te complex zonder goede cache-invalidatie bij KB-updates. |
| Re-ranking uitschakelen per org | Geen per-org configuratie voor reranking. Globale kill switch (`RERANKER_ENABLED`) volstaat. |

---

## Acceptance criteria

| # | Criterium | EARS-patroon |
|---|---|---|
| AC-1 | **When** `POST /retrieve` is called with `scope != "notebook"`, **then** the response contains a `chunks` array ordered by `reranker_score` descending, with at most `top_k` items | Event-driven |
| AC-1b | **When** `POST /retrieve` is called with `scope = "notebook"`, **then** reranking is skipped and chunks are ordered by Qdrant RRF score | State-driven |
| AC-2 | **When** `conversation_history` contains at least one prior turn, **then** a coreference resolution LLM call is made regardless of query language. If the query contains no anaphoric references, `query_resolved` equals `query`. | Event-driven |
| AC-3 | **When** coreference resolution times out (>3s) or the LLM call fails, **then** the original query is used unmodified and the request continues normally without error | Unwanted behavior |
| AC-4 | **When** the pre-retrieval gate margin exceeds `RETRIEVAL_GATE_THRESHOLD`, **then** `retrieval_bypassed = true`, `chunks` is empty, and no Qdrant query is executed | Event-driven |
| AC-5 | **When** `RETRIEVAL_GATE_ENABLED=false`, **then** the gate is skipped entirely and retrieval always proceeds | State-driven |
| AC-6 | **When** `scope = "personal"` or `scope = "both"` and `user_id` is absent, **then** the API returns HTTP 400 with a descriptive error message | Unwanted behavior |
| AC-6b | **When** `scope = "notebook"` and `notebook_id` is absent, **then** the API returns HTTP 400 | Unwanted behavior |
| AC-6c | **When** `scope = "notebook"`, **then** the Qdrant query targets `klai_focus` collection with `tenant_id + notebook_id` filter, not `klai_knowledge` | State-driven |
| AC-7 | **When** a chunk has `invalid_at` in the past, **then** it is excluded from search results regardless of scope or query | State-driven |
| AC-8 | **When** the reranker service is unavailable (timeout or HTTP error), **then** results are returned using Qdrant scores only, `reranker_score` is null in the response, and a warning is logged | Unwanted behavior |
| AC-9 | **When** `POST /chat` is called, **then** the response is a `text/event-stream` that emits `token` events followed by a final `done` event containing `citations` and `retrieval_bypassed` | Event-driven |
| AC-9b | **When** the `done` event is emitted on `/chat`, **then** `citations` contains numbered entries corresponding to `[n]` markers in the concatenated token stream | Event-driven |
| AC-10 | **When** `retrieval_bypassed = true` on `/chat`, **then** the LLM answers without KB context and the `done` event contains an empty `citations` array | Event-driven |
| AC-11 | Every request **shall** log structured fields: `org_id`, `scope`, `top_k`, `candidates_retrieved`, `retrieval_ms`, `rerank_ms`, `gate_margin`, `retrieval_bypassed` | Ubiquitous |
| AC-12 | **When** Qdrant is unavailable, **then** the API returns HTTP 503 within 5 seconds | Unwanted behavior |
| AC-13 | The service **shall** respond to `GET /health` with HTTP 200 when TEI, Qdrant, and LiteLLM are reachable | Ubiquitous |
| AC-14 | After deployment, `research-api` narrow and broad modes **shall** delegate retrieval to retrieval-api. Focus notebook query results must be functionally equivalent to the current implementation (same chunks, same ordering within reranking tolerance) | Ubiquitous |
| AC-15 | The Qdrant query **shall** always include an `invalid_at` filter that excludes expired chunks | Ubiquitous |

---

## Beslissingen voor review

De volgende punten zijn inhoudelijk open en vereisen jouw oordeel vóórdat implementatie begint:

**B1: Nieuwe service of research-api uitbreiding? ✓ BESLOTEN**
Nieuwe service. Focus en KB zijn technisch identiek; de pipeline mag maar op één plek bestaan. research-api delegeert retrieval volledig aan retrieval-api en behoudt alleen Focus UX-logica. (Zie D1.)

**B1: Nieuwe service of research-api uitbreiding? ✓ BESLOTEN**
Nieuwe service. Focus en KB zijn technisch identiek; de pipeline mag maar op één plek bestaan. research-api delegeert retrieval volledig aan retrieval-api en behoudt alleen Focus UX-logica. (Zie D1.)

**B2: Reranking — conditioneel op scope. ✓ BESLOTEN**
`scope = "notebook"` → geen reranking. Alle andere scopes → reranking altijd aan. (Zie D2.)

**B3: tei-reranker als aparte Docker service. ✓ BESLOTEN**
Aparte TEI-instantie. Als core-01 vol raakt, verplaatsen naar core-02.

**B4: TARG referentieset — synthetische generatie. ✓ BESLOTEN**
Script genereert 200 queries (100 NL + 100 EN) bij deployment via `klai-fast`. Idempotent. Uitbreidbaar met productiedata. (Zie D3.)

**B5: /chat met SSE streaming. ✓ BESLOTEN**
Streaming beïnvloedt het eindresultaat niet. Consistent met research-api. (Zie D5.)

**B6: Coreferentie meertalig — geen NL-specifieke heuristiek. ✓ BESLOTEN**
Altijd LLM-call wanneer history aanwezig is. Taal-agnostisch by design. Toekomstige overweging genoteerd in D4.

---

## Implementation Notes (sync 2026-03-26)

**Implemented as specified with two caveats documented in SPEC:**

### Vector names (actual vs SPEC)
SPEC described `dense`/`sparse`/`questions` but `klai_knowledge` collection uses `vector_chunk`/`vector_questions`/`vector_sparse`. Implemented with actual names.

### klai_focus single-vector fallback
SPEC described RRF for notebook scope, but `klai_focus` only has an unnamed single dense vector. Implemented simple cosine search fallback. Full RRF requires named vector migration (follow-up SPEC).

### Sparse vector integration deferred
`vector_sparse` not accessible from retrieval-api (no BGE-M3 sparse sidecar). Search uses `vector_chunk` + `vector_questions` RRF only.

### Commits
- `0c4bf0b` — feat(retrieval): standalone retrieval-api service (KB-008)
- `a077e98` — feat(infra): CI workflow + docker-compose entry
- `499ea03` — test(retrieval): synthesis/tei/chat coverage (90%)
- Merged to main via PR #33

### Reranker uitgeschakeld op CPU (2026-03-26)

BGE-reranker-v2-m3 (`infinity-reranker`) draait op CPU. Benchmarked op 20 documenten met gemiddeld 692 tekens: **~83 seconden**. Dat maakt reranking onbruikbaar in productie.

`RERANKER_ENABLED=false` (standaard). Reranking wordt overgeslagen; retrieval valt terug op RRF-scores uit Qdrant. De RRF hybrid search (vector_chunk + vector_questions + vector_sparse) compenseert een groot deel van de kwaliteitswinst die reranking zou bieden.

Schakel in met `RERANKER_ENABLED=true` zodra GPU-inference beschikbaar is. Cold start op GPU verwacht <500ms; ~200ms per 20 docs.

Gerelateerde commits: `22ad47d`, `836ff2c`, `7c5cdf1`, `213fb9e`
