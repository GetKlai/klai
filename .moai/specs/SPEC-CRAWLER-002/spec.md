---
id: SPEC-CRAWLER-002
version: "1.0"
status: Planned
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

# SPEC-CRAWLER-002: Crawl Registry

## Context

SPEC-CRAWLER-001 (completed 2026-03-26) implemented the bulk crawler via crawl4ai. The
current implementation has three gaps:

1. **Geen URL-dedup** — er is geen check of een URL al eerder gecrawld is. Ingest wordt
   altijd opnieuw uitgevoerd, ook als de pagina niet veranderd is.
2. **Link structuur gaat verloren** — `result.links['internal']` van crawl4ai is beschikbaar
   tijdens het crawlen maar wordt weggegooid. De relatie "pagina A linkt naar pagina B"
   wordt nergens opgeslagen.
3. **Ruwe markdown nergens opgeslagen** — bij herberekening (andere chunking, enrichment)
   moet opnieuw gecrawld worden. Er is geen cache van de broninhoud.

Deze SPEC voegt twee Postgres tabellen toe in het `knowledge` schema:
- `crawled_pages` — URL-registry met content hash en raw markdown (dedup + cache)
- `page_links` — link graph (from_url → to_url relaties)

Dit is stap 1 van een link-aware retrieval roadmap. De link graph is de basis voor
toekomstige PageRank-scoring, link expansion bij retrieval, en Graphiti wikilinks.

---

## Scope

**In scope:**
- SQL-migratie (`011_knowledge_crawl_registry.sql`) voor twee nieuwe tabellen
- Dedup + opslag in `_crawl_and_ingest_page` (bulk crawler)
- Dedup + opslag in `crawl_url` (single-URL route)
- Links opslaan in `page_links` na elke page-crawl
- Raw markdown opslaan in `crawled_pages`
- Cleanup in `delete_kb()` bij verwijderen van een KB

**Out of scope:**
- `links_to` doorsturen naar Qdrant payload
- `incoming_link_count` als scoring signal
- Graphiti integratie van wikilinks
- depth > 1 crawler fix (`max_depth` wordt nog steeds genegeerd)
- Migratie van `crawl_url` van html2text naar crawl4ai

---

## Requirements

### Module 1: Database schema

**R1-UBIQUITOUS** — De twee tabellen bestaan in het `knowledge` schema en zijn org- en kb-scoped.

```sql
-- Dedup registry + raw content cache
knowledge.crawled_pages (
    id          BIGSERIAL PRIMARY KEY,
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    url         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_markdown TEXT NOT NULL,
    crawled_at  BIGINT NOT NULL,
    UNIQUE (org_id, kb_slug, url)
)

-- Link graph
knowledge.page_links (
    id          BIGSERIAL PRIMARY KEY,
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    from_url    TEXT NOT NULL,
    to_url      TEXT NOT NULL,
    link_text   TEXT NOT NULL DEFAULT '',
    UNIQUE (org_id, kb_slug, from_url, to_url)
)
```

**R2-UBIQUITOUS** — Indexen op lookup-patronen:
- `crawled_pages`: index op `(org_id, kb_slug, url)` (dedup lookup)
- `page_links`: index op `(org_id, kb_slug, from_url)` (outgoing links per pagina)
- `page_links`: index op `(org_id, kb_slug, to_url)` (incoming links per pagina, toekomstig)

### Module 2: Bulk crawler dedup (crawler.py)

**R3-WHEN** — When `_crawl_and_ingest_page` een pagina heeft gecrawld en de markdown is
beschikbaar, THEN wordt een `content_hash` (SHA-256 hex) berekend over de markdown tekst.

**R4-WHEN** — When de `(org_id, kb_slug, url)` combinatie al bestaat in `crawled_pages`
EN de opgeslagen `content_hash` gelijk is aan de nieuwe hash, THEN wordt `ingest_document`
NIET aangeroepen (skip) en keert de functie terug.

**R5-WHEN** — When de pagina nieuw is OF de content_hash verschilt, THEN wordt:
1. `crawled_pages` geüpserved met de nieuwe hash, markdown en `crawled_at`
2. `page_links` geüpserved met alle internal links van deze pagina (bestaande links
   voor dezelfde `from_url` worden bijgewerkt via ON CONFLICT DO UPDATE)
3. `ingest_document()` aangeroepen

**R6-UBIQUITOUS** — Relatieve URLs in `result.links['internal']` worden omgezet naar
absolute URLs met `urllib.parse.urljoin(base_url=url, url=href)` vóór opslaan.

**R7-UBIQUITOUS** — Link text (`link.get("text", "")`) wordt opgeslagen in
`page_links.link_text`, afgekapt op 500 tekens.

### Module 3: Single-URL crawl dedup (crawl.py)

**R8-WHEN** — When `crawl_url` de markdown heeft berekend via html2text, THEN wordt een
`content_hash` berekend en gecheckt in `crawled_pages` op `(req.org_id, req.kb_slug,
request.url)` — de originele URL, niet het afgeleide pad.

**R9-WHEN** — When dezelfde URL al gecrawld is met dezelfde hash, THEN retourneert
`crawl_url` direct met `chunks_ingested=0` zonder `ingest_document` aan te roepen.

**R10-WHEN** — When de URL nieuw is of de hash verschilt, THEN wordt `crawled_pages`
geüpserved. Voor `crawl_url` worden geen links opgeslagen (`page_links` blijft leeg
omdat html2text geen links extraheert).

### Module 4: KB cleanup

**R11-WHEN** — When `pg_store.delete_kb(org_id, kb_slug)` wordt aangeroepen, THEN worden
ook alle rijen uit `knowledge.crawled_pages` en `knowledge.page_links` voor die
`(org_id, kb_slug)` combinatie verwijderd.

---

## Technische beslissingen

### Waarom pre-crawl dedup in `crawled_pages` naast de bestaande dedup in `ingest_document`?

`ingest_document` heeft al content-hash dedup op `knowledge.artifacts.path`. Voor de bulk
crawler is `path = url` — dit werkt. Maar de check vindt plaats NA de crawl4ai fetch. Met
`crawled_pages` kan de check plaatsvinden vóór de fetch: als hash matcht → crawl niet
eens opnieuw.

Voor `crawl_url` (single-URL route) is de bestaande dedup broken: de key is het afgeleide
pad (`livekit.md`), niet de URL. `crawled_pages` lost dit op met een URL-gebaseerde dedup.

### Waarom geen Alembic?

Het `knowledge` schema gebruikt plain SQL migraties in `deploy/postgres/migrations/`.
Alembic is alleen voor het `portal` schema (SQLAlchemy ORM in portal-api). De twee schemas
zijn gescheiden.

### `crawled_pages` als bron voor toekomstige herberekening

Raw markdown opslaan maakt het mogelijk om bij gewijzigde chunking of enrichment-logica
opnieuw te ingesteen zonder de site te hercrawlen. Dit is een directe waarde los van de
dedup functionaliteit.

---

## Out of scope (toekomstige SPECs)

| Item | Reden voor uitstel |
|------|-------------------|
| `links_to` in Qdrant payload | Vereist retrieval-layer aanpassing (aparte SPEC) |
| `incoming_link_count` scoring | Vereist aggregatie + Qdrant payload update (aparte SPEC) |
| Graphiti LINKS_TO edges | Afhankelijk van Graphiti stabilisatie |
| depth > 1 crawler | `max_depth` bug fix is een aparte, gerichte fix |
| html2text → crawl4ai in crawl_url | Product beslissing, hogere scope |
