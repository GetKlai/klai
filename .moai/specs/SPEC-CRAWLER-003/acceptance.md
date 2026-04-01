# SPEC-CRAWLER-003: Link-Graph Retrieval Enrichment -- Acceptance Criteria

**SPEC:** SPEC-CRAWLER-003
**Status:** completed

---

## Module 1: Link Graph Query Helpers (R1, R2, R3)

### Scenario 1.1: Outbound URLs ophalen

```gherkin
Given een KB "docs" voor org "org-1" met de volgende page_links:
  | from_url                    | to_url                      | link_text     |
  | https://docs.example.com/a  | https://docs.example.com/b  | Pagina B      |
  | https://docs.example.com/a  | https://docs.example.com/c  | Pagina C      |
  | https://docs.example.com/b  | https://docs.example.com/a  | Terug naar A  |
When get_outbound_urls("https://docs.example.com/a", "org-1", "docs", pool) wordt aangeroepen
Then bevat het resultaat ["https://docs.example.com/b", "https://docs.example.com/c"]
And bevat het resultaat NIET "https://docs.example.com/a"
```

### Scenario 1.2: Anchor texts ophalen met filtering

```gherkin
Given een KB "docs" voor org "org-1" met de volgende page_links naar URL X:
  | from_url | to_url | link_text        |
  | /a       | /x     | Handleiding      |
  | /b       | /x     |                  |
  | /c       | /x     |    (whitespace)  |
  | /d       | /x     | Meer informatie  |
When get_anchor_texts("/x", "org-1", "docs", pool) wordt aangeroepen
Then bevat het resultaat ["Handleiding", "Meer informatie"]
And bevat het resultaat GEEN lege of whitespace-only strings
```

### Scenario 1.3: Incoming count berekenen

```gherkin
Given een KB "docs" voor org "org-1" met 5 page_links rijen waar to_url = "/target"
When get_incoming_count("/target", "org-1", "docs", pool) wordt aangeroepen
Then is het resultaat 5
```

### Scenario 1.4: Tenant-isolatie

```gherkin
Given page_links voor org "org-1" met 3 links naar "/page"
And page_links voor org "org-2" met 7 links naar "/page"
When get_incoming_count("/page", "org-1", "docs", pool) wordt aangeroepen
Then is het resultaat 3
And worden links van org-2 NIET meegeteld
```

### Scenario 1.5: Batch incoming counts

```gherkin
Given een KB "docs" voor org "org-1" met de volgende page_links:
  | from_url | to_url |
  | /a       | /b     |
  | /a       | /c     |
  | /b       | /c     |
  | /d       | /b     |
When compute_incoming_counts("org-1", "docs", pool) wordt aangeroepen
Then is het resultaat {"/b": 2, "/c": 2}
And komt "/a" NIET voor in het resultaat (0 inkomende links)
```

---

## Module 2: Qdrant Payload Velden en Indexes (R4, R5, R6)

### Scenario 2.1: Payload indexes worden aangemaakt

```gherkin
Given een Qdrant collectie "klai_knowledge" zonder payload index op "source_url"
And zonder payload index op "incoming_link_count"
When ensure_collection() wordt uitgevoerd
Then bestaat er een keyword payload index op "source_url"
And bestaat er een integer payload index op "incoming_link_count"
```

### Scenario 2.2: links_to cap op 20

```gherkin
Given een gecrawlde pagina met 35 outbound links
When de pagina wordt geingested met link velden in extra_payload
Then bevat het payload veld "links_to" precies 20 items
And zijn dit de eerste 20 URLs uit de page_links tabel
```

### Scenario 2.3: Payload velden correct opgeslagen

```gherkin
Given een gecrawlde pagina "https://docs.example.com/guide" met:
  - 3 outbound links
  - incoming_link_count = 7
When de pagina wordt geingested
Then bevat het Qdrant payload:
  | veld                  | waarde                                          |
  | links_to              | ["https://…/a", "https://…/b", "https://…/c"]   |
  | incoming_link_count   | 7                                                |
```

---

## Module 3: Batch Update Incoming Link Counts (R7, R8)

### Scenario 3.1: Batch update link counts

```gherkin
Given Qdrant chunks voor 3 pagina's in KB "docs":
  | source_url | huidige incoming_link_count |
  | /page-a    | 0                           |
  | /page-b    | 2                           |
  | /page-c    | 5                           |
And compute_incoming_counts retourneert {"/page-a": 3, "/page-b": 2, "/page-c": 8}
When update_link_counts("org-1", "docs", {"/page-a": 3, "/page-b": 2, "/page-c": 8}) wordt aangeroepen
Then is het Qdrant payload voor /page-a chunks: incoming_link_count = 3
And is het Qdrant payload voor /page-b chunks: incoming_link_count = 2
And is het Qdrant payload voor /page-c chunks: incoming_link_count = 8
```

### Scenario 3.2: Batch job na crawl run

```gherkin
Given een bulk crawl run die 10 pagina's heeft gecrawld voor KB "docs"
When de crawl run is afgerond
Then wordt compute_incoming_counts("org-1", "docs", pool) aangeroepen
And wordt update_link_counts() aangeroepen met het resultaat
```

---

## Module 4: Anchor Text Augmentatie (R9, R10, R11)

### Scenario 4.1: Anchor text wordt toegevoegd aan enriched_text

```gherkin
Given een document met enriched_text = "Dit is de inhoud van de pagina."
And extra_payload bevat anchor_texts = ["Handleiding", "Getting Started", "Handleiding"]
When _enrich_document() het document verwerkt
Then eindigt ec.enriched_text op:
  "\n\nAnder pagina's noemen deze pagina: Handleiding | Getting Started"
And is "Handleiding" slechts een keer aanwezig (gededupliceerd)
```

### Scenario 4.2: Lege anchor_texts lijst

```gherkin
Given een document met enriched_text = "Originele tekst."
And extra_payload bevat anchor_texts = []
When _enrich_document() het document verwerkt
Then is ec.enriched_text gelijk aan "Originele tekst."
And is er GEEN anchor text blok toegevoegd
```

### Scenario 4.3: Originele text en context_prefix ongewijzigd

```gherkin
Given een document met:
  - text = "Origineel"
  - context_prefix = "Context: pagina over API"
  - enriched_text = "Context: pagina over API\n\nOrigineel"
And extra_payload bevat anchor_texts = ["API Documentatie"]
When _enrich_document() het document verwerkt
Then is text nog steeds "Origineel"
And is context_prefix nog steeds "Context: pagina over API"
And bevat alleen enriched_text het anchor text blok
```

---

## Module 5: Ingest Route Link Veld Populatie (R12)

### Scenario 5.1: Link velden worden opgehaald en toegevoegd

```gherkin
Given een ingest request met extra_payload["source_url"] = "https://docs.example.com/guide"
And page_links bevat 3 outbound links, 2 anchor texts, en incoming_count = 5
When de ingest route het document dispatcht voor enrichment
Then bevat extra_payload:
  | veld                  | waarde                           |
  | links_to              | [3 URLs]                         |
  | anchor_texts          | [2 anchor teksten]               |
  | incoming_link_count   | 5                                |
```

### Scenario 5.2: Geen source_url -- geen link velden

```gherkin
Given een ingest request zonder source_url in extra_payload
When de ingest route het document dispatcht
Then worden GEEN link_graph queries uitgevoerd
And bevat extra_payload GEEN links_to, anchor_texts, of incoming_link_count
```

---

## Module 6: Search Resultaat Uitbreiding (R13, R14, R15)

### Scenario 6.1: Nieuwe velden in search resultaat

```gherkin
Given Qdrant chunks met payload:
  | source_url                   | links_to           | incoming_link_count |
  | https://docs.example.com/a   | ["/b", "/c"]       | 5                   |
When _search_knowledge() wordt aangeroepen
Then bevat elk resultaat dict:
  - source_url = "https://docs.example.com/a"
  - links_to = ["/b", "/c"]
  - incoming_link_count = 5
```

### Scenario 6.2: Default waarden voor ontbrekende payload velden

```gherkin
Given Qdrant chunks zonder links_to en incoming_link_count in payload
When _search_knowledge() wordt aangeroepen
Then bevat elk resultaat dict:
  - links_to = []
  - incoming_link_count = 0
```

### Scenario 6.3: fetch_chunks_by_urls haalt chunks op via payload filter

```gherkin
Given Qdrant chunks met source_url in ["https://docs.example.com/b", "https://docs.example.com/c"]
And een RetrieveRequest voor org "org-1" met kb_slugs ["docs"]
When fetch_chunks_by_urls(["https://docs.example.com/b", "https://docs.example.com/c"], request, limit=10) wordt aangeroepen
Then worden chunks geretourneerd met score = 0.0
And zijn alleen chunks van org "org-1" en kb "docs" in het resultaat
```

### Scenario 6.4: fetch_chunks_by_urls met lege URL lijst

```gherkin
Given een lege URL lijst
When fetch_chunks_by_urls([], request, limit=10) wordt aangeroepen
Then is het resultaat een lege lijst
And wordt GEEN Qdrant query uitgevoerd
```

### Scenario 6.5: fetch_chunks_by_urls timeout

```gherkin
Given een Qdrant scroll query die langer dan 3 seconden duurt
When fetch_chunks_by_urls(urls, request, limit=10) wordt aangeroepen
Then wordt een lege lijst geretourneerd
And wordt een warning gelogd met "link_expand_failed"
```

---

## Module 7: 1-Hop Forward Expansion en Authority Boost (R16, R17, R18)

### Scenario 7.1: Forward expansion voegt chunks toe

```gherkin
Given link_expand_enabled = True
And de top-10 seed chunks bevatten links_to URLs naar 5 unieke pagina's
And fetch_chunks_by_urls retourneert 8 chunks voor die pagina's
And 2 van die chunks bestaan al in de raw_results (duplicate chunk_ids)
When de expansion stap in retrieve.py wordt uitgevoerd
Then worden 6 nieuwe chunks toegevoegd aan de candidate pool
And worden de 2 duplicaten NIET toegevoegd
```

### Scenario 7.2: Authority boost past score aan

```gherkin
Given link_authority_boost = 0.05
And een chunk met score = 0.8 en incoming_link_count = 10
When de authority boost wordt toegepast
Then is de nieuwe score = 0.8 + 0.05 * log(1 + 10) = 0.8 + 0.05 * 2.397 = 0.9199 (afgerond)
```

### Scenario 7.3: Expansion overgeslagen voor notebook scope

```gherkin
Given link_expand_enabled = True
And het request scope is "notebook"
When de retrieval pipeline wordt uitgevoerd
Then wordt GEEN forward expansion uitgevoerd
And worden GEEN fetch_chunks_by_urls calls gemaakt
```

### Scenario 7.4: Expansion uitgeschakeld via feature flag

```gherkin
Given link_expand_enabled = False
When de retrieval pipeline wordt uitgevoerd
Then wordt GEEN forward expansion uitgevoerd
And is de candidate pool identiek aan de RRF merge output
```

### Scenario 7.5: Authority boost uitgeschakeld

```gherkin
Given link_authority_boost = 0.0
And chunks met diverse incoming_link_count waarden
When de retrieval pipeline wordt uitgevoerd
Then worden GEEN scores aangepast op basis van incoming_link_count
```

### Scenario 7.6: Expansion met URL cap

```gherkin
Given link_expand_max_urls = 30
And de seed chunks bevatten links_to naar 50 unieke URLs
When de expansion stap wordt uitgevoerd
Then worden maximaal 30 URLs doorgegeven aan fetch_chunks_by_urls
```

---

## Module 8: Configuratie (R19, R20)

### Scenario 8.1: Standaardwaarden

```gherkin
Given geen environment variables voor link expansion instellingen
When de Settings class wordt geinitialiseerd
Then zijn de standaardwaarden:
  | setting                 | waarde |
  | link_expand_enabled     | True   |
  | link_expand_seed_k      | 10     |
  | link_expand_max_urls    | 30     |
  | link_expand_candidates  | 20     |
  | link_authority_boost    | 0.05   |
```

### Scenario 8.2: Override via environment variable

```gherkin
Given environment variable LINK_EXPAND_ENABLED = "false"
And environment variable LINK_AUTHORITY_BOOST = "0.10"
When de Settings class wordt geinitialiseerd
Then is link_expand_enabled = False
And is link_authority_boost = 0.10
```

---

## Definition of Done

- [ ] Alle bovenstaande scenario's slagen
- [ ] `link_graph.py` module aanwezig met 4 functies
- [ ] Qdrant payload indexes `source_url` (keyword) en `incoming_link_count` (integer) aanwezig
- [ ] `update_link_counts()` functie aanwezig in `qdrant_store.py`
- [ ] Anchor text augmentatie actief in `_enrich_document()`
- [ ] Search resultaten bevatten `source_url`, `links_to`, `incoming_link_count`
- [ ] `fetch_chunks_by_urls()` functie aanwezig in `search.py`
- [ ] 1-hop expansion en authority boost actief in `retrieve.py` (met feature flag)
- [ ] 5 configuratie settings aanwezig in `config.py`
- [ ] Metrics: `step_latency_seconds` label `link_expand` aanwezig
- [ ] Debug logging voor expansion statistieken
- [ ] RAGAS baseline meting voor en na Fase 1
