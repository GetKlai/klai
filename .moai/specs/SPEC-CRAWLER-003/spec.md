---
id: SPEC-CRAWLER-003
version: "1.0"
status: completed
created: 2026-04-01
updated: 2026-04-01
author: Mark Vletter
priority: high
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-01 | Mark Vletter | Initial draft |

---

# SPEC-CRAWLER-003: Link-Graph Retrieval Enrichment

## Context

SPEC-CRAWLER-002 (Crawl Registry) introduceert twee PostgreSQL tabellen in het `knowledge`
schema: `crawled_pages` en `page_links`. Deze tabellen slaan respectievelijk de gecrawlde
pagina-inhoud en de hyperlink-structuur (from_url, to_url, link_text) op.

De huidige retrieval pipeline mist drie signalen die uit deze link graph gehaald kunnen worden:

1. **Anchor text vocabulaire** -- andere pagina's beschrijven een target pagina vaak met
   andere woorden dan de pagina zelf gebruikt. Dit vocabulaire-verschil is een bekende oorzaak
   van recall-verlies bij keyword- en sparse-vector zoeken (Stanford IR Book, anchor text hoofdstuk).

2. **Structurele autoriteit** -- pagina's met veel inkomende links zijn redactioneel belangrijk.
   Dit signaal is complementair aan de bestaande entity PageRank in Graphiti (die over
   LLM-geextraheerde concepten loopt, niet over hyperlinks).

3. **1-hop forward expansion** -- wanneer een relevant zoekresultaat naar andere pagina's linkt,
   zijn die gelinkte pagina's vaak ook relevant (SAGE 2025: +5.7 recall@20 op OTT-QA).
   De huidige pipeline kent alleen vector similarity, geen structurele traversal.

Dit SPEC voegt deze drie signalen toe in twee fasen:
- **Fase 1 (Ingest):** Link graph queries, Qdrant payload velden, anchor text augmentatie
- **Fase 2 (Retrieval):** 1-hop forward expansion, authority boost, configureerbare settings

Onderzoeksbasis: `docs/research/link-graph-rag.md` (systeem-agnostisch) en
`docs/research/link-graph-applied.md` (Klai-specifiek ontwerp).

---

## Scope

**In scope:**
- Nieuwe module `knowledge_ingest/link_graph.py` met async query helpers tegen `page_links`
- Nieuwe Qdrant payload velden (`links_to`, `incoming_link_count`) en payload indexes
- Anchor text augmentatie in `enrichment_tasks._enrich_document()`
- Ingest route: link velden populeren in `extra_payload` voor enrichment task dispatch
- Batch job: `incoming_link_count` refreshen na afloop van een crawl run
- Retrieval: `source_url`, `links_to`, `incoming_link_count` teruggeven in search resultaten
- Retrieval: `fetch_chunks_by_urls()` voor payload-filter gebaseerde chunk ophaling
- Retrieval: 1-hop forward expansion + authority boost in `retrieve.py`
- 5 nieuwe configuratie-instellingen in `retrieval_api/config.py`

**Out of scope:**
- Wijzigingen aan `knowledge.crawled_pages` of `knowledge.page_links` tabellen (eigendom SPEC-CRAWLER-002)
- `[:LINKS_TO]` edges in Graphiti/FalkorDB (te complex om te retrofitten, Phase 3)
- `linked_from: list[url]` in Qdrant payload (O(n) write fan-out probleem)
- Backward expansion (inkomende links -- hub pagina's domineren context)
- Structurele PageRank (PPR over link graph) -- entity PageRank bestaat al
- Nieuwe API endpoints of nieuwe Docker services
- Backward-compatible migratie van bestaande Qdrant chunks (backfill is optioneel follow-up)

---

## Afhankelijkheden

| Dependency | Type | Status |
|------------|------|--------|
| SPEC-CRAWLER-002 (Crawl Registry) | Hard -- leest uit `knowledge.page_links` | Planned |
| SPEC-CRAWLER-001 (Bulk Crawler) | Soft -- `source_url` in artifact `extra` | Completed |
| Qdrant `klai_knowledge` collectie | Runtime | Bestaand |
| `enrichment_tasks._enrich_document()` | Code | Bestaand |
| `qdrant_store.set_entity_graph_data()` | Pattern reference | Bestaand |
| `retrieval_api/api/retrieve.py` RRF merge | Code | Bestaand |

---

## Requirements

### Module 1: Link graph query helpers (`link_graph.py`)

**R1-UBIQUITOUS** -- Het systeem biedt vier async query functies tegen `knowledge.page_links`:
- `get_outbound_urls(url, org_id, kb_slug, pool) -> list[str]`
- `get_anchor_texts(url, org_id, kb_slug, pool) -> list[str]`
- `get_incoming_count(url, org_id, kb_slug, pool) -> int`
- `compute_incoming_counts(org_id, kb_slug, pool) -> dict[str, int]`

**R2-UBIQUITOUS** -- Alle queries zijn org- en kb-scoped via `org_id` en `kb_slug` parameters.

**R3-UBIQUITOUS** -- `get_anchor_texts()` filtert lege en whitespace-only link teksten uit.

### Module 2: Qdrant payload velden en indexes

**R4-UBIQUITOUS** -- Elk ingested chunk van een gecrawlde pagina bevat twee nieuwe payload velden:
- `links_to: list[str]` -- outbound URLs van de bronpagina (max 20 items)
- `incoming_link_count: int` -- aantal pagina's dat naar deze pagina linkt

**R5-WHEN** -- When `ensure_collection()` draait en de payload indexes voor `source_url`
(keyword) en `incoming_link_count` (integer) ontbreken, THEN worden deze indexes aangemaakt.

**R6-UBIQUITOUS** -- De `links_to` lijst is beperkt tot maximaal 20 URLs. Bij meer dan 20
outbound links worden de eerste 20 opgeslagen (volgorde uit `page_links` tabel).

### Module 3: Batch update incoming link counts

**R7-WHEN** -- When een nieuwe functie `qdrant_store.update_link_counts(org_id, kb_slug,
url_to_count)` wordt aangeroepen, THEN wordt `incoming_link_count` via `set_payload()`
bijgewerkt voor alle chunks met een matchende `source_url`, `org_id`, en `kb_slug`.

**R8-WHEN** -- When een volledige crawl run (bulk crawler) is afgerond, THEN wordt
`compute_incoming_counts()` aangeroepen en het resultaat doorgegeven aan
`update_link_counts()` om de telling voor alle pagina's in de KB te verversen.

### Module 4: Anchor text augmentatie

**R9-WHEN** -- When `_enrich_document()` een document verwerkt EN `extra_payload` een
niet-lege `anchor_texts` lijst bevat, THEN wordt een gededupliceerde anchor text blok
toegevoegd aan `ec.enriched_text` voor elke enriched chunk, VOOR de embedding stap.

**R10-UBIQUITOUS** -- Het anchor text blok heeft het formaat:
`"\n\nAnder pagina's noemen deze pagina: {anchor_text_1} | {anchor_text_2} | ..."`

**R11-UBIQUITOUS** -- De anchor text augmentatie wijzigt alleen `enriched_text` (dat de
dense + sparse vectors aanstuurt). De originele `text` en `context_prefix` blijven ongewijzigd.

### Module 5: Ingest route link veld populatie

**R12-WHEN** -- When de ingest route een document dispatcht voor enrichment EN `source_url`
beschikbaar is in `extra_payload`, THEN worden `links_to`, `anchor_texts`, en
`incoming_link_count` opgehaald uit `page_links` via `link_graph.py` en toegevoegd aan
`extra_payload`.

### Module 6: Search resultaat uitbreiding

**R13-WHEN** -- When `_search_knowledge()` resultaten retourneert, THEN bevat elk resultaat
drie extra velden: `source_url`, `links_to` (default `[]`), en `incoming_link_count`
(default `0`), gelezen uit de Qdrant payload.

**R14-UBIQUITOUS** -- Een nieuwe functie `fetch_chunks_by_urls(urls, request, limit)` haalt
chunks op via een payload filter op het geindexeerde `source_url` veld, scoped op `org_id`
en `kb_slugs`. Chunks worden geretourneerd met `score=0.0` (scored door de reranker).

**R15-UBIQUITOUS** -- `fetch_chunks_by_urls()` gebruikt `client.scroll()` (geen vector query
nodig) met een timeout van 3 seconden.

### Module 7: 1-hop forward expansion en authority boost

**R16-WHEN** -- When `link_expand_enabled` is `True` EN het scope niet `notebook` is,
THEN worden na de RRF merge en voor de reranker de volgende stappen uitgevoerd:
1. Uit de top-N seed chunks (configureerbaar via `link_expand_seed_k`) worden alle
   `links_to` URLs verzameld en gededupliceerd
2. `fetch_chunks_by_urls()` wordt aangeroepen met maximaal `link_expand_max_urls` URLs
   en `link_expand_candidates` als limit
3. Resultaatchunks worden gededupliceerd tegen bestaande resultaten (op `chunk_id`)
4. Nieuwe chunks worden toegevoegd aan de candidate pool

**R17-WHEN** -- When `link_authority_boost` groter is dan 0, THEN wordt voor elke chunk
in de candidate pool de score verhoogd met `link_authority_boost * log(1 + incoming_link_count)`.

**R18-IF** -- Het systeem past GEEN forward expansion toe op scope `notebook` (notebooks
bevatten geen hyperlinks).

### Module 8: Configuratie

**R19-UBIQUITOUS** -- Vijf nieuwe instellingen in `retrieval_api/config.py`:
- `link_expand_enabled: bool = True` -- schakelaar voor 1-hop expansion
- `link_expand_seed_k: int = 10` -- aantal top chunks om links uit te extraheren
- `link_expand_max_urls: int = 30` -- maximaal aantal URLs om te expanderen
- `link_expand_candidates: int = 20` -- maximaal aantal chunks uit expansion
- `link_authority_boost: float = 0.05` -- gewicht voor `log(1+incoming_link_count)` score modifier

**R20-UBIQUITOUS** -- Alle expansion kan uitgeschakeld worden door `link_expand_enabled=false`
te zetten als environment variable, zonder codewijziging.

---

## Technische beslissingen

### Waarom geen `linked_from: list[url]` in Qdrant payload?

Elke nieuw gecrawlde pagina die naar een target linkt zou een update vereisen van alle
bestaande chunks van die target -- O(n) write fan-out. `incoming_link_count: int` is een
enkel getal dat via batch refresh wordt bijgewerkt, met minimale schrijfkosten.

### Waarom geen backward expansion?

Hub pagina's (index, sitemap, navigatie) hebben veel inkomende links en domineren de
context bij backward expansion. Forward expansion volgt de informatiestructuur van de
auteur en is veiliger (SAGE 2025, HopRAG 2025).

### Waarom `score=0.0` voor expansion chunks?

Expansion chunks zijn niet gevonden via vector similarity maar via structurele traversal.
Ze krijgen score 0.0 zodat de reranker (niet de RRF merge) bepaalt of ze relevant zijn.
Dit voorkomt dat irrelevante gelinkte pagina's boven echte matches komen.

### Waarom log(1 + count) als authority boost?

Logaritmische demping voorkomt dat pagina's met zeer veel inkomende links (bijv. homepage)
disproportioneel hoog scoren. De configureerbare weight (default 0.05) houdt het signaal
bescheiden ten opzichte van semantische relevantie.

### Waarom anchor text in enriched_text en niet als apart veld?

Door anchor text in `enriched_text` te bakken wordt het meegenomen in zowel de dense als
sparse vector embedding. Dit overbrugt vocabulaire-mismatches automatisch, zonder
een apart retrieval-pad nodig te hebben.

---

## Out of scope (toekomstige SPECs)

| Item | Reden voor uitstel |
|------|-------------------|
| `[:LINKS_TO]` edges in Graphiti | Non-triviaal om te retrofitten; Cypher op FalkorDB vereist apart node-type design |
| Backward expansion | Hub pagina's domineren context; forward is veiliger |
| Structurele PPR over link graph | Entity PageRank bestaat al; pas evalueren na Phase 2 |
| Backfill bestaande Qdrant chunks | Optionele follow-up; nieuwe crawls krijgen de velden automatisch |
| Anchor text staleness tracking | Vereist tracking welke target chunks re-embedding nodig hebben bij link text wijziging |
