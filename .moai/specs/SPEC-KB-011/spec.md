# SPEC-KB-011: Graphiti + FalkorDB Knowledge Graph Layer

> Status: COMPLETED (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-005 (contextual retrieval), SPEC-KB-008 (retrieval-api), SPEC-KB-009 (docs-sync via Gitea webhooks)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md`
> Created: 2026-03-26

---

## Wat bestaat er vandaag

De kennislaag verwerkt documenten via `knowledge-ingest` (FastAPI) en slaat ze op in twee vormen:

1. **Qdrant** (`klai_knowledge` collection): dense + sparse + HyPE embeddings per chunk, hybrid search via RRF (KB-007)
2. **PostgreSQL** (`knowledge.artifacts`): metadata per document met bi-temporele velden (`belief_time_start`, `belief_time_end`), JSONB `extra` kolom

Retrieval verloopt via `retrieval-api` (KB-008): coreferentie-resolutie, pre-retrieval gate, hybrid search, cross-encoder reranking. Alle retrieval is chunk-gebaseerd: de pipeline vindt tekst-fragmenten die semantisch op de query lijken.

**Wat ontbreekt:** er is geen relatie-gebaseerde retrieval. Vragen als "Welke producten heeft klant X afgenomen?" of "Wie is verantwoordelijk voor beleid Y?" vereisen entity-relatie-traversal, niet cosine-similarity op tekst-chunks. De huidige pipeline mist multi-hop reasoning: feiten die verspreid staan over meerdere documenten worden niet verbonden.

---

## Wat deze SPEC bouwt

Een knowledge graph laag die naast de bestaande vector-retrieval pipeline draait, aangedreven door **Graphiti** (Zep) met **FalkorDB** als graph database.

Concreet:

1. **FalkorDB** als nieuwe Docker service op core-01
2. **Graphiti integratie in `knowledge-ingest`**: na de bestaande enrichment-stappen (contextual retrieval, HyPE, embeddings naar Qdrant) wordt elk document ook als Graphiti episode verwerkt
3. **Graph search in `retrieval-api`**: voor queries die baat hebben bij relatie-traversal, wordt naast Qdrant ook Graphiti's search bevraagd, resultaten samengevoegd via RRF
4. **episode_id bridge**: Graphiti's episode_id wordt opgeslagen in `knowledge.artifacts.extra` voor traceability

Na deze SPEC bestaat de retrieval-pipeline uit twee complementaire paden:
- **Vector pad** (bestaand): semantische chunk-retrieval via Qdrant
- **Graph pad** (nieuw): entity-relatie-traversal via Graphiti/FalkorDB

---

## Architectuur

### Ingest-uitbreiding

```
Document aangeleverd (POST /ingest/v1/document of webhook)
    |
    v
[Bestaande pipeline - ongewijzigd]
    chunker -> contextual retrieval -> HyPE -> embedder -> Qdrant
    |
    v
[Nieuw: Graphiti episode ingest]
    graphiti.add_episode(
        name=artifact_id,
        episode_body=document_text,
        source=EpisodeType.text,
        source_description=content_type,
        reference_time=belief_time_start,
        group_id=org_id
    )
    |
    v
[Graphiti doet intern via LLM:]
    entity extractie -> entity resolution (dedup) -> relatie-extractie
    -> bi-temporele edge-opslag -> contradiction detection
    |
    v
[Bridge: sla episode_id op]
    UPDATE knowledge.artifacts SET extra = extra || '{"graphiti_episode_id": "..."}'
    WHERE id = artifact_id
```

### Retrieval-uitbreiding

```
Query binnenkomt op retrieval-api
    |
    v
[Bestaande pipeline]
    coreferentie-resolutie -> pre-retrieval gate -> hybrid search (Qdrant) -> reranking
    |                                                                          |
    |  [Parallel, nieuw]                                                       |
    +-> Graphiti search(query, group_id=org_id)                                |
    |   -> entities + edges + communities                                      |
    |                                                                          |
    v                                                                          v
[Merge: RRF over Qdrant chunks + Graphiti results]
    |
    v
Response
```

### Service topologie

```
core-01 Docker services:

[knowledge-ingest]  --graphiti SDK-->  [FalkorDB :6380]
                    --LLM calls--->   [LiteLLM]  (entity extraction via klai-fast)

[retrieval-api]     --graphiti SDK-->  [FalkorDB :6380]
                    --qdrant------->  [Qdrant]
```

---

## Design decisions

### D1: Graphiti als episode-processor, geen custom entity extraction

**Gekozen: Graphiti end-to-end pipeline.**

Graphiti doet entity extraction, entity resolution (deduplicatie), relatie-extractie, en contradiction detection in een geintegreerde pipeline. Elk document wordt als "episode" aangeboden; Graphiti handelt de rest af via LLM-calls.

Alternatieven overwogen en afgewezen:
- Custom entity extraction + handmatig graph-opbouw: te veel code, te veel edge cases, geen contradiction detection
- Alleen entity extraction zonder graph: verliest de multi-hop traversal die het doel is

**Trade-off:** Graphiti maakt LLM-calls per episode voor entity extraction. Dit verhoogt de ingest-latency en LLM-kosten. Acceptabel omdat ingest async is (geen gebruiker wacht erop) en het volume beheersbaar is (honderden artikelen, niet miljoenen).

### D2: FalkorDB in plaats van Neo4j

**Gekozen: FalkorDB (SSPLv1 licentie).**

Neo4j Community Edition bevat "non-production" taal in de licentiedocumentatie. FalkorDB SSPLv1 is geschikt voor intern self-hosted gebruik. FalkorDB draait als Redis module en is lichtgewicht op core-01.

Graphiti ondersteunt FalkorDB native via `graphiti-core[falkordb]` met een dedicated `FalkorDriver`.

### D3: FalkorDB poort en isolatie

**Gekozen: poort 6380, gescheiden van bestaande Redis.**

FalkorDB draait als Redis module maar mag niet de bestaande Redis-instantie vervangen of delen. Aparte container op poort 6380 voorkomt conflicten.

### D4: reference_time mapping

**Gekozen: `belief_time_start` uit `knowledge.artifacts` als `reference_time`.**

Graphiti's `reference_time` parameter bepaalt wanneer de feiten in het document geldig werden. Dit correspondeert precies met `belief_time_start` in het bestaande schema (KB-004). Voor documenten zonder expliciete `belief_time_start` wordt `created_at` als fallback gebruikt.

### D5: Tenant isolation via group_id

**Gekozen: `org_id` als Graphiti `group_id`.**

Graphiti's `group_id` creert geisoleerde namespaces binnen een enkele instantie. Elke organisatie krijgt een eigen graph-namespace. Search queries bevatten altijd `group_id=org_id`, waardoor cross-tenant data-lekkage onmogelijk is.

### D6: Ingest volgorde -- Qdrant eerst, Graphiti daarna

**Gekozen: Graphiti ingest als laatste stap, asynchroon na Qdrant.**

Graphiti episode-ingest is trager dan Qdrant upsert (LLM-calls voor entity extraction). De bestaande pipeline mag niet vertraagd worden. Aanpak:

1. Bestaande pipeline (chunking, embedding, Qdrant upsert) draait ongewijzigd
2. Na succesvolle Qdrant-opslag wordt Graphiti episode-ingest als background task gestart
3. Als Graphiti-ingest faalt, is het document alsnog doorzoekbaar via vector-retrieval. Graph-enrichment wordt geretried via een retry-mechanisme.

Dit voorkomt dat een Graphiti-storing de hele ingest-pipeline blokkeert.

### D7: LLM model voor Graphiti entity extraction

**Gekozen: `klai-fast` via LiteLLM.**

Graphiti maakt intern LLM-calls voor entity extraction en resolution. Graphiti's LLM-client wordt geconfigureerd met de LiteLLM proxy URL en `klai-fast` als model. `klai-fast` biedt voldoende kwaliteit voor entity extraction tegen lagere kosten dan `klai-primary`.

Graphiti ondersteunt een custom LLM-client configuratie. De exacte integratie moet geverifieerd worden bij implementatie (`/moai:2-run`).

### D8: Graph search routing in retrieval-api

**Gekozen: parallelle uitvoering, altijd-aan voor niet-notebook scopes.**

Graph search draait parallel met Qdrant search voor alle scopes behalve `notebook` (Focus-documenten zitten niet in de graph). Resultaten worden samengevoegd via RRF.

Rationale: een intent-classifier die bepaalt of een query "relatie-gericht" is, vereist gelabelde data die er niet is. Altijd-parallel is conservatief: als de graph geen relevante resultaten oplevert, domineert Qdrant in de RRF-merge. De extra latency is acceptabel omdat Graphiti search en Qdrant search parallel draaien.

Toekomstige overweging: als graph search significant bijdraagt aan latency zonder verbetering voor niet-relatie-queries, kan een classifier worden toegevoegd. Dit is niet in scope voor KB-011.

### D9: Superseded documents en contradiction detection

**Gekozen: Graphiti's ingebouwde contradiction detection.**

Wanneer een KB-artikel wordt gewijzigd (KB-009 webhook voor edits), wordt de nieuwe versie als nieuwe episode ingediend bij Graphiti. Graphiti detecteert automatisch contradicties tussen de oude en nieuwe episode en markeert oude feiten als `invalid_at` + `expired_at`. Er is geen extra code nodig.

Voor bulk re-ingest (hele KB opnieuw verwerken): gebruik `add_episode_bulk` met de kanttekening dat edge invalidation niet wordt uitgevoerd tijdens bulk-ingest. Na bulk-ingest moet een enkele reguliere re-ingest van gewijzigde documenten worden gedaan om contradictions op te lossen.

---

## Nieuwe service: FalkorDB

**Docker service:** `falkordb`

**Image:** `falkordb/falkordb:latest`

**Port:** 6380 (extern) -> 6379 (intern, Redis protocol)

**Persistentie:** volume mount naar `/opt/klai/falkordb-data`

**docker-compose entry:**
```yaml
falkordb:
  image: falkordb/falkordb:latest
  ports:
    - "6380:6379"
  volumes:
    - /opt/klai/falkordb-data:/data
  restart: unless-stopped
```

**Configuratie:** geen authenticatie vereist voor lokale FalkorDB instantie (Docker network isolatie volstaat).

---

## Changes aan bestaande services

### `knowledge-ingest`: Graphiti episode ingest

**Nieuwe dependency:** `graphiti-core[falkordb]`

**Nieuwe module:** `knowledge_ingest/graph.py`

Bevat:
- `GraphitiClient` class: initialiseert Graphiti met FalkorDriver, configureert LLM-client voor `klai-fast`
- `async ingest_episode(artifact_id, document_text, org_id, content_type, belief_time_start)`: roept `graphiti.add_episode()` aan, slaat episode_id op in artifacts.extra
- Retry-logica: max 3 pogingen met exponential backoff bij Graphiti/FalkorDB fouten

**Integratiepunt:** na succesvolle Qdrant-opslag in de bestaande ingest-flow, wordt `ingest_episode()` als background task aangeboden.

**Configuratie (env vars):**
```
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
GRAPHITI_ENABLED=true
GRAPHITI_LLM_MODEL=klai-fast
```

### `retrieval-api`: graph search

**Nieuwe dependency:** `graphiti-core[falkordb]`

**Nieuwe module:** `retrieval_api/services/graph_search.py`

Bevat:
- `GraphSearchService` class: initialiseert Graphiti read-only client
- `async search(query, org_id, top_k)`: roept `graphiti.search(query, group_id=org_id)` aan, converteert resultaten naar chunk-achtig formaat voor RRF-merge

**Integratiepunt in pipeline (stap 3 uitbreiding):**

```python
# Bestaande stap 3: Qdrant hybrid search
qdrant_task = asyncio.create_task(search_qdrant(query_resolved, scope_filter, candidates))

# Nieuwe parallel pad: Graphiti search (alleen voor niet-notebook scopes)
graph_task = None
if scope != "notebook" and settings.GRAPHITI_ENABLED:
    graph_task = asyncio.create_task(graph_search.search(query_resolved, org_id, top_k=20))

qdrant_results = await qdrant_task
graph_results = await graph_task if graph_task else []

# Merge via RRF
merged = rrf_merge(qdrant_results, graph_results)
```

**Graceful degradation:** als graph search faalt (FalkorDB down, timeout), worden alleen Qdrant-resultaten gebruikt. Warning gelogd, geen user-facing error.

**Configuratie (env vars):**
```
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
GRAPHITI_ENABLED=true
GRAPH_SEARCH_TIMEOUT=5.0
```

**Response uitbreiding:** het `metadata` object in de `/retrieve` response krijgt een nieuw veld:
```json
{
  "metadata": {
    "candidates_retrieved": 60,
    "graph_results_count": 5,
    "reranked_to": 8,
    "retrieval_ms": 42,
    "graph_search_ms": 180,
    "rerank_ms": 315,
    "gate_margin": 0.31
  }
}
```

---

## Wat NIET in scope is

| Item | Waarom niet nu |
|---|---|
| Graph-only retrieval endpoint | Geen apart endpoint voor graph search. Altijd gecombineerd met vector search via bestaande `/retrieve`. |
| Focus documenten in graph | Focus (notebook scope) documenten worden niet in Graphiti ingediend. Focus is persoonlijk en kortstondig; graph-enrichment voegt weinig toe. |
| Custom entity types | Graphiti's standaard entity extraction volstaat voor de eerste 200 KB-artikelen. Custom types evalueren na productie-ervaring. |
| Graph visualization UI | Geen UI voor het bekijken van de knowledge graph. Kan later als portal-feature worden gebouwd. |
| Community detection tuning | Graphiti doet standaard community detection. Geen tuning in eerste versie. |
| Per-org graph configuratie | Alle orgs gebruiken dezelfde Graphiti-instellingen. Geen per-org entity types of extraction prompts. |
| MCP server voor graph queries | Een MCP wrapper rond graph search is een aparte integratie. |
| Kùzu | Gearchiveerd oktober 2025, overgeslagen. |
| PostgreSQL-native graph | Expliciet afgewezen (zie beslissingen boven SPEC). |

---

## Acceptance criteria

| # | Criterium | EARS-patroon |
|---|---|---|
| AC-1 | **When** a document is ingested via `POST /ingest/v1/document`, **then** after successful Qdrant storage, a Graphiti episode is created with `group_id` matching the document's `org_id` and `reference_time` matching `belief_time_start` | Event-driven |
| AC-2 | **When** Graphiti episode ingest completes, **then** the returned `episode_id` is stored in `knowledge.artifacts.extra` as `graphiti_episode_id` | Event-driven |
| AC-3 | **When** Graphiti episode ingest fails (FalkorDB unavailable, LLM timeout, or Graphiti error), **then** the document remains searchable via Qdrant vector retrieval, a warning is logged, and the episode ingest is retried up to 3 times with exponential backoff | Unwanted behavior |
| AC-4 | **When** a KB article is updated (KB-009 edit webhook fires) and re-ingested, **then** Graphiti processes the new version as a new episode, and contradicted facts from the previous version are automatically marked with `invalid_at` by Graphiti's contradiction detection | Event-driven |
| AC-5 | **When** `POST /retrieve` is called with `scope != "notebook"` and `GRAPHITI_ENABLED=true`, **then** graph search runs in parallel with Qdrant search, and results are merged via RRF before reranking | Event-driven |
| AC-6 | **When** `POST /retrieve` is called with `scope = "notebook"`, **then** graph search is not executed regardless of `GRAPHITI_ENABLED` setting | State-driven |
| AC-7 | **When** graph search fails (FalkorDB unavailable or timeout >5s), **then** the response contains only Qdrant results, `graph_results_count` in metadata is 0, and a warning is logged | Unwanted behavior |
| AC-8 | **When** `GRAPHITI_ENABLED=false`, **then** no Graphiti operations are performed during ingest or retrieval, and the system behaves identically to pre-KB-011 | State-driven |
| AC-9 | The `/retrieve` response metadata **shall** include `graph_results_count` and `graph_search_ms` fields | Ubiquitous |
| AC-10 | **When** two documents from different `org_id` values are ingested, **then** graph search with one `org_id` returns zero entities from the other org's documents (tenant isolation via `group_id`) | Event-driven |
| AC-11 | The FalkorDB container **shall** persist data to a mounted volume, surviving container restarts without data loss | Ubiquitous |
| AC-12 | **When** `GET /health` is called on retrieval-api and `GRAPHITI_ENABLED=true`, **then** FalkorDB connectivity is included in the health check | Event-driven |
| AC-13 | Every Graphiti episode ingest **shall** log structured fields: `artifact_id`, `org_id`, `episode_id`, `entity_count`, `edge_count`, `ingest_ms` | Ubiquitous |
| AC-14 | Graphiti entity extraction **shall** use `klai-fast` via LiteLLM proxy, never direct model names | Ubiquitous |

---

## TRUST 5 checklist

| Pillar | Status | Notes |
|---|---|---|
| **Tested** | TODO | Integration tests voor Graphiti ingest + retrieval merge. Mock FalkorDB voor unit tests. End-to-end test met 10 KB-artikelen. |
| **Readable** | TODO | Graph module volgt bestaande knowledge-ingest patterns. Docstrings op publieke functies. |
| **Unified** | TODO | ruff + black formatting. Pydantic v2 models consistent met bestaande codebase. |
| **Secured** | TODO | Tenant isolation via group_id. Geen cross-org data lekkage. FalkorDB alleen bereikbaar via Docker network. |
| **Trackable** | TODO | Conventional commits met `feat(knowledge):` prefix. Verwijzing naar SPEC-KB-011. |

---

## Implementatie-volgorde

### Primair doel: FalkorDB + Graphiti ingest

1. FalkorDB Docker service toevoegen aan docker-compose
2. `graphiti-core[falkordb]` dependency toevoegen aan knowledge-ingest
3. `graph.py` module implementeren met GraphitiClient
4. Ingest-flow uitbreiden: background task na Qdrant-opslag
5. episode_id bridge naar artifacts.extra
6. Testen met 5-10 KB-artikelen

### Secundair doel: retrieval-api graph search

7. `graphiti-core[falkordb]` dependency toevoegen aan retrieval-api
8. `graph_search.py` service implementeren
9. Parallel graph search integreren in retrieval pipeline (stap 3)
10. RRF merge uitbreiden voor graph resultaten
11. Response metadata uitbreiden
12. Health check uitbreiden

### Finaal doel: validatie met 200 Voys KB-artikelen

13. Bulk ingest van alle 200 artikelen
14. Re-ingest van gewijzigde artikelen voor contradiction detection
15. Vergelijk retrieval-kwaliteit: vector-only vs vector+graph
16. Tune graph search gewicht in RRF merge

---

## Pre-run checklist

> **[HARD] Verify alle versies via PyPI/GitHub voor je pinned in requirements.txt.**
> Training cutoff = augustus 2025. Gebruik altijd actuele versies, niet wat het model kent.

Bij start van `/run`:
1. `graphiti-core` — check https://pypi.org/project/graphiti-core/ voor latest + changelog
2. `falkordb` Python client — check https://pypi.org/project/falkordb/
3. Minimale `pydantic` versie voor de gekozen graphiti-core versie
4. FalkorDB Docker image — check https://hub.docker.com/r/falkordb/falkordb/tags voor latest stable
5. Compatibiliteit controleren met huidige service-stack (asyncpg, FastAPI, pydantic-settings)

Dan pas pinnen in requirements.txt.

---

## Implementation Notes

**Completion date:** 2026-03-26

**All 14 acceptance criteria confirmed met** (AC-1 through AC-14). See progress.md for full verification log.

**Key deployment fix — FalkorDriver import and constructor:**
- Correct import path: `from graphiti_core.driver.falkordb_driver import FalkorDriver` (not `graphiti_core.driver.falkordb`)
- Correct constructor: `Graphiti(graph_driver=FalkorDriver(...))` — uses `graph_driver=` keyword argument; no positional uri/user/password arguments
- `graph.py` imports wrapped in `try/except` with `_GRAPHITI_AVAILABLE` flag for graceful degradation when `graphiti-core` is not installed

**Live on core-01 since 2026-03-26.**
