# Implementation Plan: SPEC-CRAWLER-002

## Technology stack

- **Language:** Python 3.12
- **DB driver:** asyncpg (raw SQL, no ORM)
- **Migration:** Plain SQL in `deploy/postgres/migrations/`
- **Test framework:** pytest + pytest-asyncio (existing pattern)

## Task decomposition

### Task 1 — SQL migration

**File:** `deploy/postgres/migrations/011_knowledge_crawl_registry.sql`

```sql
-- Migration: 011_knowledge_crawl_registry.sql
-- Crawl registry: URL dedup, raw markdown cache, and link graph for knowledge-ingest.
-- crawled_pages: one row per (org_id, kb_slug, url) — upserted on every successful crawl.
-- page_links:    one row per (org_id, kb_slug, from_url, to_url) — upserted per crawled page.

CREATE TABLE IF NOT EXISTS knowledge.crawled_pages (
    id           BIGSERIAL PRIMARY KEY,
    org_id       TEXT    NOT NULL,
    kb_slug      TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    raw_markdown TEXT    NOT NULL,
    crawled_at   BIGINT  NOT NULL,
    CONSTRAINT crawled_pages_uniq UNIQUE (org_id, kb_slug, url)
);

CREATE INDEX IF NOT EXISTS idx_crawled_pages_lookup
    ON knowledge.crawled_pages (org_id, kb_slug, url);

CREATE TABLE IF NOT EXISTS knowledge.page_links (
    id        BIGSERIAL PRIMARY KEY,
    org_id    TEXT NOT NULL,
    kb_slug   TEXT NOT NULL,
    from_url  TEXT NOT NULL,
    to_url    TEXT NOT NULL,
    link_text TEXT NOT NULL DEFAULT '',
    CONSTRAINT page_links_uniq UNIQUE (org_id, kb_slug, from_url, to_url)
);

CREATE INDEX IF NOT EXISTS idx_page_links_outgoing
    ON knowledge.page_links (org_id, kb_slug, from_url);

CREATE INDEX IF NOT EXISTS idx_page_links_incoming
    ON knowledge.page_links (org_id, kb_slug, to_url);
```

**Deploy:** Run on core-01 after merge:
```bash
docker exec klai-core-postgres-1 psql -U postgres -d klai \
  -f /docker-entrypoint-initdb.d/migrations/011_knowledge_crawl_registry.sql
```

---

### Task 2 — pg_store helpers

**File:** `klai-knowledge-ingest/knowledge_ingest/pg_store.py`

Add two helper functions:

```python
async def upsert_crawled_page(
    org_id: str,
    kb_slug: str,
    url: str,
    content_hash: str,
    raw_markdown: str,
    crawled_at: int,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.crawled_pages
            (org_id, kb_slug, url, content_hash, raw_markdown, crawled_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (org_id, kb_slug, url)
        DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            raw_markdown  = EXCLUDED.raw_markdown,
            crawled_at    = EXCLUDED.crawled_at
        """,
        org_id, kb_slug, url, content_hash, raw_markdown, crawled_at,
    )


async def get_crawled_page_hash(org_id: str, kb_slug: str, url: str) -> str | None:
    """Return stored content_hash for this URL, or None if not yet crawled."""
    pool = await get_pool()
    return await pool.fetchval(
        "SELECT content_hash FROM knowledge.crawled_pages "
        "WHERE org_id = $1 AND kb_slug = $2 AND url = $3",
        org_id, kb_slug, url,
    )


async def upsert_page_links(
    org_id: str,
    kb_slug: str,
    from_url: str,
    links: list[dict],  # [{"href": "...", "text": "..."}, ...]
) -> None:
    """Upsert all outgoing links for from_url. Resolves relative URLs."""
    from urllib.parse import urljoin  # noqa: PLC0415
    pool = await get_pool()
    for link in links:
        href = link.get("href", "")
        if not href:
            continue
        to_url = urljoin(from_url, href)
        link_text = (link.get("text", "") or "")[:500]
        await pool.execute(
            """
            INSERT INTO knowledge.page_links
                (org_id, kb_slug, from_url, to_url, link_text)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (org_id, kb_slug, from_url, to_url)
            DO UPDATE SET link_text = EXCLUDED.link_text
            """,
            org_id, kb_slug, from_url, to_url, link_text,
        )
```

Also extend `delete_kb` to include the new tables:
```python
# In delete_kb, add after existing DELETE FROM knowledge.crawl_jobs:
await pool.execute(
    "DELETE FROM knowledge.crawled_pages WHERE org_id = $1 AND kb_slug = $2",
    org_id, kb_slug,
)
await pool.execute(
    "DELETE FROM knowledge.page_links WHERE org_id = $1 AND kb_slug = $2",
    org_id, kb_slug,
)
```

---

### Task 3 — Bulk crawler dedup (`_crawl_and_ingest_page`)

**File:** `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py`

Current flow (line 109–138):
```python
result = await crawler.arun(url=url, config=config)
if not result.success:
    raise ValueError(...)
text = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
...
await ingest_document(IngestRequest(...))
```

New flow — add dedup block and link saving before `ingest_document`:
```python
result = await crawler.arun(url=url, config=config)
if not result.success:
    raise ValueError(...)

text = result.markdown.fit_markdown or result.markdown.raw_markdown or ""

import hashlib  # (already imported in the module via ingest.py — add at top-level)
from knowledge_ingest import pg_store  # noqa: PLC0415

content_hash = hashlib.sha256(text.encode()).hexdigest()
stored_hash = await pg_store.get_crawled_page_hash(org_id, kb_slug, url)

if stored_hash is not None and stored_hash == content_hash:
    logger.info("crawl_skipped_unchanged", url=url, org_id=org_id, kb_slug=kb_slug)
    return  # skip ingest — content unchanged

# Upsert crawled_pages
await pg_store.upsert_crawled_page(
    org_id=org_id,
    kb_slug=kb_slug,
    url=url,
    content_hash=content_hash,
    raw_markdown=text,
    crawled_at=int(time.time()),
)

# Upsert page_links
if result.links:
    await pg_store.upsert_page_links(
        org_id=org_id,
        kb_slug=kb_slug,
        from_url=url,
        links=result.links.get("internal", []),
    )

# Then: existing ingest
await ingest_document(IngestRequest(...))
```

Note: `hashlib` must be added to the top-level imports in `crawler.py` (currently imported
inline only via ingest.py).

---

### Task 4 — Single-URL crawl dedup (`crawl_url`)

**File:** `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py`

Current flow (line 175–193):
```python
markdown = converter.handle(resp.text)
...
ingest_req = IngestRequest(org_id=request.org_id, kb_slug=request.kb_slug, ...)
result = await ingest_document(ingest_req)
```

New flow — add dedup block after html2text conversion:
```python
markdown = converter.handle(resp.text)

import hashlib  # add at top-level imports
from knowledge_ingest import pg_store  # add at top-level imports

content_hash = hashlib.sha256(markdown.encode()).hexdigest()
stored_hash = await pg_store.get_crawled_page_hash(
    request.org_id, request.kb_slug, request.url
)

if stored_hash is not None and stored_hash == content_hash:
    logger.info("Crawl skipped (unchanged): %s", request.url)
    return CrawlResponse(url=request.url, path=path, chunks_ingested=0)

await pg_store.upsert_crawled_page(
    org_id=request.org_id,
    kb_slug=request.kb_slug,
    url=request.url,      # store the original URL, not the derived path
    content_hash=content_hash,
    raw_markdown=markdown,
    crawled_at=int(time.time()),
)

# No page_links: html2text does not extract links in structured form
ingest_req = IngestRequest(...)
result = await ingest_document(ingest_req)
```

---

### Task 5 — Tests

**Files:**
- `klai-knowledge-ingest/tests/test_crawl_registry_dedup.py`
- `klai-knowledge-ingest/tests/test_page_links.py`

**Test cases to cover:**

1. `test_bulk_crawl_skip_unchanged` — when stored_hash == new hash, ingest_document is NOT called
2. `test_bulk_crawl_reingest_on_change` — when hash differs, ingest_document IS called and crawled_pages updated
3. `test_bulk_crawl_new_page` — when URL not in crawled_pages, insert + ingest
4. `test_page_links_saved` — upsert_page_links resolves relative URLs and stores them
5. `test_page_links_relative_url_resolution` — `../help` resolved correctly against base URL
6. `test_single_url_skip_unchanged` — crawl_url returns chunks_ingested=0 when hash matches
7. `test_single_url_url_key` — crawled_pages is keyed on request.url, not the derived path
8. `test_delete_kb_cleans_registry` — delete_kb removes crawled_pages and page_links rows

Use `unittest.mock.AsyncMock` to mock `pg_store` and `ingest_document`. Follow the
existing pattern in `tests/test_ingest_content_hash_dedup.py`.

---

## Implementation order

| # | Task | File(s) | Risk |
|---|------|---------|------|
| 1 | SQL migration | `deploy/postgres/migrations/011_...sql` | Low |
| 2 | pg_store helpers | `knowledge_ingest/pg_store.py` | Low |
| 3 | Bulk crawler dedup | `knowledge_ingest/adapters/crawler.py` | Medium |
| 4 | Single-URL dedup | `knowledge_ingest/routes/crawl.py` | Low |
| 5 | delete_kb cleanup | `knowledge_ingest/pg_store.py` | Low |
| 6 | Tests | `tests/test_crawl_registry_dedup.py` etc. | Low |

## Deployment steps

```bash
# 1. Apply migration (after merge, before service restart)
ssh core-01 "docker exec klai-core-postgres-1 psql -U postgres -d klai \
  < /path/to/011_knowledge_crawl_registry.sql"

# 2. Rebuild and restart knowledge-ingest
# (handled by CI / deploy pipeline)
```

## Dependencies and risks

| Risk | Mitigation |
|------|-----------|
| `_JS_PREPARE_PAGE` ImportError bug in crawler.py | This SPEC does not fix it — it exists independently. Import of pg_store should be at module level, not inside the function where the bug occurs. |
| Link href values empty or malformed | `if not href: continue` guard in `upsert_page_links` |
| Very large pages (500KB+ markdown) | `text[:500_000]` guard already exists via `IngestRequest.content = Field(max_length=500_000)` — same limit applies to `raw_markdown` storage |
| Concurrent crawl jobs for same org/kb | ON CONFLICT DO UPDATE handles safely |
