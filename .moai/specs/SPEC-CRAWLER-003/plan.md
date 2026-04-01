# SPEC-CRAWLER-003: Link-Graph Retrieval Enrichment -- Implementation Plan

**SPEC:** SPEC-CRAWLER-003
**Status:** Planned
**Priority:** High
**Dependency:** SPEC-CRAWLER-002 (hard -- `knowledge.page_links` moet bestaan)

---

## Overzicht

Twee-fasen implementatie:
- **Fase 1 (Ingest):** Link graph queries, Qdrant payload velden, anchor text augmentatie. Geen wijzigingen aan de retrieval pipeline. Laagste risico.
- **Fase 2 (Retrieval):** 1-hop forward expansion, authority boost, configureerbare settings. Beperkt risico door caps en feature flag.

---

## Milestone 1: Link Graph Query Module (Priority High)

**Doel:** Nieuwe module `link_graph.py` met async query functies tegen `knowledge.page_links`.

**Requirements:** R1, R2, R3

### Taken

1. Maak `knowledge_ingest/link_graph.py` aan met vier functies:
   - `get_outbound_urls()` -- SELECT to_url WHERE from_url = $url
   - `get_anchor_texts()` -- SELECT link_text WHERE to_url = $url, filter lege strings
   - `get_incoming_count()` -- SELECT COUNT(*) WHERE to_url = $url
   - `compute_incoming_counts()` -- SELECT to_url, COUNT(*) GROUP BY to_url

2. Alle queries gebruiken `org_id` en `kb_slug` parameters voor tenant-isolatie

### Bestanden

| Bestand | Actie |
|---------|-------|
| `knowledge_ingest/link_graph.py` | NIEUW |

### Risico's

- Laag -- pure read queries tegen bestaande tabel met bestaande indexes (SPEC-CRAWLER-002 R2)

---

## Milestone 2: Qdrant Payload Velden en Indexes (Priority High)

**Doel:** Twee nieuwe payload velden en twee nieuwe payload indexes in Qdrant.

**Requirements:** R4, R5, R6, R7

### Taken

1. In `qdrant_store.ensure_collection()`: voeg `source_url` (keyword) en
   `incoming_link_count` (integer) toe aan de payload index lijst

2. Implementeer `update_link_counts(org_id, kb_slug, url_to_count)`:
   - Itereer over url_to_count dict
   - Gebruik `client.set_payload()` met Filter op `org_id` + `kb_slug` + `source_url`
   - Zelfde patroon als bestaande `set_entity_graph_data()`

3. Cap `links_to` op 20 items in de ingest flow

### Bestanden

| Bestand | Actie |
|---------|-------|
| `knowledge_ingest/qdrant_store.py` | WIJZIG -- `ensure_collection()` + nieuwe functie |

### Risico's

- Laag -- payload index toevoegen is een idempotente operatie
- `update_link_counts()` is batch; voor grote KB's met 1000+ URLs kan dit traag zijn. Mitigatie: logging van batch grootte en duur

---

## Milestone 3: Anchor Text Augmentatie (Priority High)

**Doel:** Anchor text vocabulaire meenemen in dense + sparse vector embeddings.

**Requirements:** R9, R10, R11

### Taken

1. In `enrichment_tasks._enrich_document()`, na het opbouwen van `enriched_text`:
   - Lees `anchor_texts` uit `extra_payload`
   - Dedupliceer met `dict.fromkeys()` (behoud volgorde)
   - Voeg toe als `"\n\nAnder pagina's noemen deze pagina: {tekst1} | {tekst2} | ..."`
   - Alleen toevoegen aan `enriched_text`, niet aan `text` of `context_prefix`

### Bestanden

| Bestand | Actie |
|---------|-------|
| `knowledge_ingest/enrichment_tasks.py` | WIJZIG -- `_enrich_document()` |

### Risico's

- Medium -- anchor text verlengt `enriched_text`, wat de embedding kwaliteit kan beinvloeden. Mitigatie: anchor block komt NA de bestaande context prefix + HyPE vragen, dus het zit aan het einde van de tekst. Bij excessief lange anchor blocks (veel linkers) kan truncatie door het embedding model optreden. Mitigatie: alleen unieke anchor texts, met een impliciete cap door het aantal pagina's dat linkt.

---

## Milestone 4: Ingest Route Link Veld Populatie (Priority High)

**Doel:** Link velden populeren in `extra_payload` voordat de enrichment task wordt gedispatched.

**Requirements:** R8, R12

### Taken

1. In de crawl/ingest route (na SPEC-CRAWLER-001), voor dispatch van enrichment task:
   - Check of `source_url` aanwezig is in `extra_payload`
   - Indien ja: roep `link_graph.get_outbound_urls()`, `get_anchor_texts()`, `get_incoming_count()` aan
   - Voeg `links_to`, `anchor_texts`, `incoming_link_count` toe aan `extra_payload`

2. Na afloop van een volledige bulk crawl run:
   - Roep `link_graph.compute_incoming_counts()` aan
   - Roep `qdrant_store.update_link_counts()` aan met het resultaat

### Bestanden

| Bestand | Actie |
|---------|-------|
| `knowledge_ingest/routes/crawl.py` | WIJZIG -- link veld populatie + batch job |

### Risico's

- Medium -- drie extra DB queries per pagina bij ingest. Mitigatie: queries zijn indexed en lightweight (SPEC-CRAWLER-002 R2 garandeert indexes). Batch job na crawl run is asynchroon.

---

## Milestone 5: Search Resultaat Uitbreiding (Priority Medium)

**Doel:** Nieuwe payload velden beschikbaar maken in search resultaten + chunk ophaling via URL.

**Requirements:** R13, R14, R15

### Taken

1. In `search._search_knowledge()` resultaat dict: voeg `source_url`, `links_to`,
   `incoming_link_count` toe (gelezen uit Qdrant payload met defaults)

2. Implementeer `fetch_chunks_by_urls(urls, request, limit)`:
   - Payload filter: `source_url IN urls` + scope conditions + `_invalid_at_filter()`
   - Gebruik `client.scroll()` met `with_vectors=False`
   - Timeout: 3 seconden
   - Return chunk dicts met `score=0.0`

### Bestanden

| Bestand | Actie |
|---------|-------|
| `retrieval_api/services/search.py` | WIJZIG -- result dict + nieuwe functie |

### Risico's

- Laag -- payload filter queries zijn efficient op geindexeerd veld
- `scroll()` zonder vector is goedkoper dan een search query

---

## Milestone 6: 1-Hop Forward Expansion en Authority Boost (Priority Medium)

**Doel:** Structurele link-gebaseerde retrieval signalen toevoegen aan de pipeline.

**Requirements:** R16, R17, R18

### Taken

1. In `retrieve.py`, na de RRF merge stap en voor de reranker:
   - Extract `links_to` uit top-N seed chunks (`link_expand_seed_k`)
   - Dedupliceer en cap op `link_expand_max_urls`
   - Roep `fetch_chunks_by_urls()` aan
   - Dedupliceer tegen bestaande resultaten op `chunk_id`
   - Voeg nieuwe chunks toe aan candidate pool

2. Authority boost:
   - Voor elke chunk: `score += link_authority_boost * log(1 + incoming_link_count)`
   - Alleen als `link_authority_boost > 0`

3. Voeg metrics toe: `step_latency_seconds.labels(step="link_expand")`

4. Voeg debug logging toe voor expansion statistieken

### Bestanden

| Bestand | Actie |
|---------|-------|
| `retrieval_api/api/retrieve.py` | WIJZIG -- expansion + authority boost |

### Risico's

- Medium -- expansion voegt chunks toe aan de reranker input. Mitigatie: capped op `link_expand_candidates` (default 20), en `link_expand_enabled` flag voor instant uitschakeling.
- Latency risico: extra Qdrant scroll query. Mitigatie: 3s timeout, metrics tracking.

---

## Milestone 7: Configuratie Settings (Priority Medium)

**Doel:** Alle expansion parameters configureerbaar via environment variables.

**Requirements:** R19, R20

### Taken

1. Voeg 5 nieuwe velden toe aan de Settings class in `config.py`:
   - `link_expand_enabled: bool = True`
   - `link_expand_seed_k: int = 10`
   - `link_expand_max_urls: int = 30`
   - `link_expand_candidates: int = 20`
   - `link_authority_boost: float = 0.05`

### Bestanden

| Bestand | Actie |
|---------|-------|
| `retrieval_api/config.py` | WIJZIG -- 5 nieuwe settings |

### Risico's

- Laag -- pydantic Settings met standaardwaarden

---

## Implementatievolgorde

```
Milestone 7 (config)          -- geen dependencies, kan parallel
    |
Milestone 1 (link_graph.py)   -- geen dependencies
    |
    +-- Milestone 2 (qdrant indexes + update_link_counts)
    |       |
    +-- Milestone 3 (anchor text augmentatie)
    |       |
    +-------+-- Milestone 4 (ingest route populatie)
                    |
                    |  --- Fase 1 compleet ---
                    |
Milestone 5 (search resultaat)
    |
Milestone 6 (expansion + authority boost) -- hangt af van M5 + M7
```

**Fase 1** (Milestones 1-4): Pure ingest-side wijzigingen. Geen impact op bestaande
retrieval pipeline. Kan gevalideerd worden met RAGAS recall metingen voor en na.

**Fase 2** (Milestones 5-7): Retrieval-side wijzigingen. Feature flag
(`link_expand_enabled`) maakt instant rollback mogelijk.

---

## Validatiestrategie

### Fase 1 validatie

- RAGAS evaluation op een gecrawlde KB voor en na Phase 1 ingest
- Controleer of `recall@20` toeneemt (anchor text signaal)
- Inspecteer Qdrant payloads: `links_to` en `incoming_link_count` aanwezig
- Controleer dat `enriched_text` het anchor text blok bevat

### Fase 2 validatie

- Meet latency impact: `step_latency_seconds` metric voor `link_expand` stap
- RAGAS evaluation met expansion aan vs. uit
- Controleer dat expansion chunks door de reranker worden gescoord
- Test met `link_expand_enabled=false` om feature flag werking te verifieren

---

## Overzicht gewijzigde bestanden

### klai-knowledge-ingest

| Bestand | Actie | Milestone |
|---------|-------|-----------|
| `knowledge_ingest/link_graph.py` | NIEUW | M1 |
| `knowledge_ingest/qdrant_store.py` | WIJZIG | M2 |
| `knowledge_ingest/enrichment_tasks.py` | WIJZIG | M3 |
| `knowledge_ingest/routes/crawl.py` | WIJZIG | M4 |

### klai-retrieval-api

| Bestand | Actie | Milestone |
|---------|-------|-----------|
| `retrieval_api/services/search.py` | WIJZIG | M5 |
| `retrieval_api/api/retrieve.py` | WIJZIG | M6 |
| `retrieval_api/config.py` | WIJZIG | M7 |

### Service boundaries

- klai-knowledge-ingest leest uit `knowledge.page_links` (PostgreSQL)
- klai-retrieval-api leest uit Qdrant payload velden -- geen directe DB toegang
- Geen nieuwe API endpoints
- Geen nieuwe Docker services
