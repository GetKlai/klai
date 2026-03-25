# SPEC: Web Crawler Connector (Crawl4AI)

> Status: 📋 DRAFT — 2026-03-25
> Architecture reference: `claude-docs/klai-knowledge-architecture.md` §4.1, §14
> Related: `deploy/docker-compose.yml`

---

## Context & beslissing

**Firecrawl** draait al op `klai-net` — maar uitsluitend voor LibreChat (chat/Focus mode,
single-page scrapes). Dat blijft zo.

**Crawl4AI** wordt de scraper voor de knowledge ingest pipeline. Motivatie:

| Criterium | Firecrawl | Crawl4AI | Winnaar |
|-----------|-----------|----------|---------|
| RAG-output kwaliteit | Goed | Specifiek geoptimaliseerd | Crawl4AI |
| Markdown semantische structuur | Basis | Heading-aware, LLM-geoptimaliseerd | Crawl4AI |
| Python-native (past in onze stack) | REST only | SDK + REST | Crawl4AI |
| Extra dependencies | Postgres + RabbitMQ | Alleen Playwright | Crawl4AI |
| Licentie | AGPL | Apache 2.0 | Crawl4AI |
| Al deployed | Ja (voor LibreChat) | Nee (nieuw) | Firecrawl |

Conclusie: Firecrawl is er omdat LibreChat het vereist, niet omdat het de beste RAG-scraper is.
Voor ingest-kwaliteit die retrieval direct beïnvloedt, is Crawl4AI de juiste keuze.

---

## Goal

Organisaties kunnen websites als kennisbron toevoegen in het Klai portal.
Een gebruiker vult een base URL in (bijv. `https://docs.example.com`), en het systeem
crawlt + ingest alle matchende pagina's in hun knowledge base via de bestaande
connector → ingest pipeline.

---

## Architecture fit

```
Portal UI
  └─ POST /api/connectors (type: "web_crawler", config: {base_url, max_depth, ...})
       │
       ▼
klai-connector (WebCrawlerAdapter)
  ├─ list_documents()  →  Crawl4AI REST API  (async crawl job, alle pagina's als Markdown)
  ├─ fetch_document()  →  uit cache (content al opgehaald tijdens list_documents)
  └─ get_cursor_state()  →  {last_crawl_at, url_count}
       │
       ▼  POST /ingest/v1/document (content: markdown, per pagina)
knowledge-ingest
  ├─ chunker (markdown-aware heading splits)
  ├─ embedder (BGE-M3 dense via TEI)
  └─ qdrant_store (klai_knowledge, scoped by org_id + kb_slug)

Separate services op klai-net:
  firecrawl-api:3002  →  LibreChat only (ongewijzigd)
  crawl4ai:11235      →  klai-connector only (nieuw)
```

---

## Crawl4AI deployment

Crawl4AI heeft een officiële Docker image met ingebouwde REST API server.

```yaml
# toe te voegen aan deploy/docker-compose.yml
crawl4ai:
  image: unclecode/crawl4ai:latest
  restart: unless-stopped
  environment:
    CRAWL4AI_API_TOKEN: ${CRAWL4AI_INTERNAL_KEY}
  networks:
    - klai-net
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 2G
```

- Geen Postgres, geen RabbitMQ — Crawl4AI is stateless (Playwright + asyncio)
- Niet exposed via Caddy — internal Docker network only
- `CRAWL4AI_INTERNAL_KEY` toe te voegen aan `.env` en `.env.sops`

---

## Crawl4AI API endpoints gebruikt

| Actie | Endpoint | Beschrijving |
|-------|----------|--------------|
| Start crawl job | `POST /crawl` | Async job starten, geeft `task_id` terug |
| Poll job status | `GET /task/{task_id}` | Wacht op completion, haalt resultaten op |
| Health check | `GET /health` | Liveness check |

---

## Connector config schema

```python
class WebCrawlerConfig(BaseModel):
    base_url: HttpUrl                        # Root URL (bijv. https://docs.example.com)
    max_depth: int = 3                       # Link depth vanaf base_url
    allowed_path_prefix: str | None = None  # Beperken tot /docs/, /help/, etc.
    exclude_patterns: list[str] = []        # Patronen om over te slaan
    max_pages: int = 500                    # Veiligheidslimiet
```

Opgeslagen als JSON in de `connectors` tabel.

---

## Phase 1 — Crawl4AI Docker service

**Doel:** Crawl4AI draaien als interne service op `klai-net`.

Taken:
- `CRAWL4AI_INTERNAL_KEY` genereren + toevoegen aan `.env` en `.env.sops`
- Service toevoegen aan `deploy/docker-compose.yml`
- Deployen op core-01, health check verifiëren

---

## Phase 2 — WebCrawlerAdapter in klai-connector

**Locatie:** `deploy/klai-connector/app/adapters/webcrawler.py`

### list_documents()

```
POST http://crawl4ai:11235/crawl
  body: {
    urls: [base_url],
    crawler_config: {
      deep_crawl_strategy: {
        type: "BFS",
        max_depth: max_depth,
        max_pages: max_pages,
        filter_chain: [
          { type: "URLPatternFilter", patterns: [allowed_path_prefix] }  # indien ingesteld
        ]
      },
      scraping_strategy: {
        type: "LXMLWebScrapingStrategy"   # sneller dan default voor statische sites
      },
      markdown_generator: {
        type: "DefaultMarkdownGenerator",
        options: { ignore_links: false, body_width: 0 }
      }
    }
  }
  returns: { task_id: "uuid" }

Poll GET http://crawl4ai:11235/task/{task_id}
  until status == "completed"
  returns: { results: [{ url, markdown, metadata: { title, ... } }, ...] }

Cache results per URL voor fetch_document().

Return: list[DocumentRef]
  - path: URL path component (bijv. "docs/api/reference")
  - ref:  full URL
  - size: len(markdown) in bytes
  - content_type: ".html"
```

### fetch_document()

Content is al opgehaald tijdens `list_documents()` — teruggeven vanuit cache.

```python
return self._crawl_cache[ref.ref].encode("utf-8")
```

### get_cursor_state()

```python
{
    "last_crawl_at": datetime.now(UTC).isoformat(),
    "url_count": len(discovered_urls),
    "base_url": str(connector.config["base_url"]),
}
```

Incrementele sync: altijd full re-crawl (Qdrant dedupliceert via path upsert).
Optioneel V2: URL-count vergelijken; skip als identiek en `last_crawl_at` recent.

---

## Phase 3 — Adapter factory in klai-connector

`deploy/klai-connector/app/main.py` hardcodeert nu `GitHubAdapter`.
Uitbreiden met factory pattern:

```python
def get_adapter(connector_type: str, settings: Settings) -> BaseAdapter:
    match connector_type:
        case "github":
            return GitHubAdapter(settings)
        case "web_crawler":
            return WebCrawlerAdapter(settings)
        case _:
            raise ValueError(f"Unknown connector type: {connector_type}")
```

`WebCrawlerAdapter` krijgt `CRAWL4AI_API_URL` (= `http://crawl4ai:11235`) en
`CRAWL4AI_INTERNAL_KEY` uit settings.

---

## Phase 4 — Portal: connector type UI

"Website" toevoegen als connector type in de portal frontend.

Velden:
- **Base URL** — verplicht, gevalideerd als HTTPS URL
- **Pad-prefix** — optioneel (bijv. `/docs/`)
- **Max pagina's** — optioneel, default 500
- **Sync frequentie** — handmatig / dagelijks / wekelijks

Portal backend slaat `type: "web_crawler"` op in de `connectors` tabel.

---

## Phase 5 — Architectuurdoc bijwerken

`claude-docs/klai-knowledge-architecture.md §14`: "Crawl4AI" is al correct —
alleen toevoegen dat Firecrawl er apart naast staat voor LibreChat.

---

## Implementatievolgorde

| # | Taak | Raakt aan |
|---|------|-----------|
| 1 | Crawl4AI Docker service deployen | `deploy/docker-compose.yml`, `.env` |
| 2 | `WebCrawlerAdapter` implementeren | `klai-connector/app/adapters/webcrawler.py` |
| 3 | `WebCrawlerConfig` Pydantic model | `klai-connector/app/models/` |
| 4 | Settings uitbreiden | `klai-connector/app/settings.py` |
| 5 | Adapter factory in `main.py` | `klai-connector/app/main.py` |
| 6 | Portal frontend: "Website" connector type | `portal/frontend/` |
| 7 | Portal backend: `web_crawler` type accepteren | `portal/backend/app/api/connectors.py` |
| 8 | Architectuurdoc bijwerken | `claude-docs/klai-knowledge-architecture.md` |
| 9 | Integratietest: crawl → ingest → retrieve | `klai-connector/tests/` |

---

## Out of scope (V1)

- JavaScript-heavy SPAs met complexe auth flows
- Per-pagina change detection (hash-based) — full re-crawl is voldoende
- Rate limiting per domein (Crawl4AI beheert dit intern)
- Sitemap.xml als primaire discovery (Crawl4AI's BFS-strategie dekt dit)

---

## Open vragen

1. **Polling timeout:** ✅ Async job tracking (industry standard).
   `list_documents()` submitted de crawl job en slaat `task_id` op in `cursor_state`.
   Bij de volgende sync-trigger checkt de adapter of de job klaar is via `GET /task/{task_id}`.
   SyncEngine blokkeert nooit op een langlopende crawl. Vereist kleine uitbreiding van SyncEngine
   om "job pending" state te herkennen en de ingest-stap over te slaan totdat de job compleet is.

2. **Max pages default:** ✅ Default 200, maximum 2.000.
   Gebruiker moet bewust omhoog zetten voor grote sites. Focused knowledge bases presteren
   beter voor retrieval dan brede dumps. Config: `max_pages: int = 200, Field(le=2000)`.

3. **Statische vs JS-sites:** ✅ Altijd `LXMLWebScrapingStrategy` — geen Playwright, nooit.
   Als een site niet indexeerbaar is zonder browser-rendering, is dat de verantwoordelijkheid
   van de site-eigenaar. Wij crawlen wat publiek toegankelijk is via HTTP. Playwright is
   te traag en te zwaar voor een background ingest pipeline.

4. **Error handling:** ✅ Waarschuwen in sync log, sync gaat door.
   Pagina's die 404, 403 of leeg Markdown retourneren worden overgeslagen.
   De SyncRun krijgt een `warnings` lijst met de gefaalde URLs zodat de gebruiker
   het kan zien in de portal. Harde fout alleen bij volledige crawl-job failure.
