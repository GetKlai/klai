# Acceptance Criteria: SPEC-CRAWLER-002

## AC-1: Bulk crawl skip op ongewijzigde content (R4)

**Given** een pagina `https://help.voys.nl/livekit` is eerder gecrawld voor `(org_id="org1", kb_slug="voys-help")`
en de opgeslagen `content_hash` in `knowledge.crawled_pages` is `abc123`

**When** `_crawl_and_ingest_page` dezelfde URL crawlt en `hashlib.sha256(text.encode()).hexdigest()` geeft `abc123` terug

**Then**
- `ingest_document` wordt NIET aangeroepen
- `knowledge.crawled_pages` bevat nog steeds de originele rij (geen update)
- De functie geeft terug zonder exception
- Log bevat `crawl_skipped_unchanged` event met `url`, `org_id`, `kb_slug`

---

## AC-2: Bulk crawl ingest op gewijzigde content (R5)

**Given** een pagina `https://help.voys.nl/livekit` is eerder gecrawld met hash `abc123`

**When** `_crawl_and_ingest_page` dezelfde URL crawlt en de nieuwe hash is `def456` (content gewijzigd)

**Then**
- `knowledge.crawled_pages` wordt geüpserved met de nieuwe `content_hash`, `raw_markdown` en `crawled_at`
- `knowledge.page_links` wordt geüpserved met de links van deze pagina
- `ingest_document` wordt aangeroepen met de nieuwe content

---

## AC-3: Bulk crawl eerste keer (R3, R5)

**Given** een pagina `https://help.voys.nl/nieuw` staat NIET in `knowledge.crawled_pages`

**When** `_crawl_and_ingest_page` deze URL crawlt

**Then**
- Een nieuwe rij wordt ingevoegd in `knowledge.crawled_pages` met `url`, `content_hash`, `raw_markdown`, `crawled_at`
- `knowledge.page_links` bevat rijen voor alle internal links van deze pagina
- `ingest_document` wordt aangeroepen

---

## AC-4: Link URL-resolutie (R6, R7)

**Given** een pagina `https://help.voys.nl/docs/` retourneert internal links:
```python
[
    {"href": "/docs/api", "text": "API Reference"},
    {"href": "https://help.voys.nl/docs/faq", "text": "FAQ"},
    {"href": "../support", "text": "Support" * 200},  # lange tekst
]
```

**When** `upsert_page_links` wordt aangeroepen met `from_url="https://help.voys.nl/docs/"` en deze links

**Then**
- `to_url` voor `/docs/api` is `https://help.voys.nl/docs/api` (relatief → absoluut)
- `to_url` voor `https://help.voys.nl/docs/faq` is ongewijzigd
- `to_url` voor `../support` is `https://help.voys.nl/support`
- `link_text` voor de lange tekst is afgekapt op maximaal 500 tekens
- Lege `href` waarden worden overgeslagen

---

## AC-5: Single-URL route skip op ongewijzigde content (R8, R9)

**Given** `POST /ingest/v1/crawl` met `url="https://help.voys.nl/livekit"` is eerder uitgevoerd
en `knowledge.crawled_pages` bevat een rij voor `(org_id, kb_slug, "https://help.voys.nl/livekit")` met hash `abc123`

**When** dezelfde request opnieuw binnenkomt en html2text produceert dezelfde markdown (hash `abc123`)

**Then**
- `ingest_document` wordt NIET aangeroepen
- Response is `{"url": "https://help.voys.nl/livekit", "path": "...", "chunks_ingested": 0}`
- HTTP status 200

---

## AC-6: Single-URL route — URL als dedup-key, niet het afgeleide pad (R8, R10)

**Given** twee crawls voor dezelfde URL `https://help.voys.nl/livekit` met identieke content
maar de eerste crawl produceerde path `livekit.md` en de tweede crawl zou opnieuw dezelfde URL crawlen

**When** de tweede `POST /ingest/v1/crawl` binnenkomt

**Then**
- De check in `crawled_pages` gebruikt `request.url` (`https://help.voys.nl/livekit`) als key, NIET `livekit.md`
- De tweede crawl wordt correct overgeslagen (zelfde hash)

---

## AC-7: KB verwijderen ruimt registry op (R11)

**Given** `knowledge.crawled_pages` en `knowledge.page_links` bevatten rijen voor `(org_id="org1", kb_slug="voys-help")`

**When** `pg_store.delete_kb(org_id="org1", kb_slug="voys-help")` wordt aangeroepen

**Then**
- Alle rijen in `knowledge.crawled_pages` voor `(org_id="org1", kb_slug="voys-help")` zijn verwijderd
- Alle rijen in `knowledge.page_links` voor `(org_id="org1", kb_slug="voys-help")` zijn verwijderd
- Rijen voor andere org/kb combinaties zijn ongewijzigd

---

## AC-8: Concurrent upsert veiligheid

**Given** twee crawl jobs draaien tegelijk voor dezelfde `(org_id, kb_slug, url)`

**When** beide `upsert_crawled_page` aanroepen voor dezelfde URL

**Then**
- Geen database fout (ON CONFLICT DO UPDATE afhandelt dit)
- De uiteindelijke rij in `crawled_pages` bevat de meest recente `crawled_at` waarde

---

## Kwaliteitseisen

| Gate | Eis |
|------|-----|
| Testdekking | ≥85% voor gewijzigde en nieuwe modules |
| Lint | `ruff check` zonder fouten |
| Types | `pyright` zonder fouten |
| Migratie | Idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) |
| Backward compat | Bestaande crawl-jobs zonder registry werken ongewijzigd |
