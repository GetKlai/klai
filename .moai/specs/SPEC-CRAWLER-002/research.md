# Research: SPEC-CRAWLER-002 — Crawl Registry

**Generated:** 2026-04-01
**Scope:** klai-knowledge-ingest service — bulk crawler + single-URL crawl route

---

## Architecture analysis

### Files involved

| File | Role |
|------|------|
| `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` | Bulk crawl job (crawl4ai) |
| `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py` | Single-URL crawl route (html2text) |
| `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` | Core ingest pipeline |
| `klai-knowledge-ingest/knowledge_ingest/pg_store.py` | asyncpg query helpers |
| `klai-knowledge-ingest/knowledge_ingest/db.py` | asyncpg pool singleton |
| `deploy/postgres/migrations/` | Plain-SQL migration files |

### Migration system

The `knowledge` schema uses **plain SQL files** in `deploy/postgres/migrations/`, not Alembic. The portal uses Alembic for the `portal` schema — that is separate.

Naming convention: `NNN_description.sql`. Current last file: `010_assertion_mode_taxonomy.sql`.
New file: **`011_knowledge_crawl_registry.sql`**.

Format (from `006_knowledge_crawl_jobs.sql`):
```sql
-- Migration: 006_knowledge_crawl_jobs.sql
-- One-line comment.

CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
    id TEXT PRIMARY KEY,
    ...
);
```

---

## Function signatures

### `run_crawl_job` (crawler.py:16–95)

```python
async def run_crawl_job(
    job_id: str,
    org_id: str,
    kb_slug: str,
    start_url: str,
    max_depth: int = 2,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    rate_limit: float = 2.0,
    content_selector: str | None = None,
) -> None:
```

Flow:
1. Crawls `start_url` with crawl4ai `AsyncWebCrawler`
2. Collects `result.links.get("internal", [])` — list of `{"href": "...", ...}` dicts — from the start URL only
3. For each URL: calls `_crawl_and_ingest_page(crawler, config, url, org_id, kb_slug, delay)`
4. Updates `knowledge.crawl_jobs` progress with raw asyncpg

**Known bug (out of scope):** `max_depth` is accepted but never used. Also imports
`_JS_PREPARE_PAGE` from `crawl.py` (line 46) but this symbol does not exist there —
causes `ImportError` at runtime (out of scope of this SPEC).

### `_crawl_and_ingest_page` (crawler.py:98–138)

```python
async def _crawl_and_ingest_page(
    crawler: object,
    config: object,
    url: str,
    org_id: str,
    kb_slug: str,
    delay: float,
) -> None:
```

Flow:
1. `result = await crawler.arun(url=url, config=config)` — crawl4ai result
2. `text = result.markdown.fit_markdown or result.markdown.raw_markdown or ""`
3. Calls `ingest_document(IngestRequest(org_id=org_id, kb_slug=kb_slug, path=url, content=text, source_type="connector", content_type="kb_article"|"pdf_document", synthesis_depth=1, extra={"source_url": url, "crawled_at": ...}))`

**Critical finding:** `result.links` is available inside `_crawl_and_ingest_page` after `crawler.arun()` — it contains the links found on each crawled page. These links are currently thrown away. This is the correct injection point for `page_links`.

**Data available at injection point:**
- `url` (absolute URL of this page)
- `org_id`, `kb_slug` (passed as parameters)
- `text` (markdown, already computed)
- `result.links.get("internal", [])` — list of `{"href": "...", ...}` dicts, possibly relative URLs

### `ingest_document` (ingest.py:181–347)

```python
async def ingest_document(req: IngestRequest) -> dict:
```

Already has path-based content-hash dedup (line 187–196):
```python
content_hash = hashlib.sha256(req.content.encode()).hexdigest()
stored_hash = await pg_store.get_active_content_hash(req.org_id, req.kb_slug, req.path)
if stored_hash is not None and stored_hash == content_hash:
    return {"status": "skipped", "reason": "content unchanged", "chunks": 0}
```

For the bulk crawler, `path = url` (full URL), so this dedup works — but only AFTER crawl4ai has already fetched the page. `crawled_pages` adds a pre-crawl dedup layer: check the hash before fetching.

### `crawl_url` (crawl.py:152–193)

```python
@router.post("/ingest/v1/crawl", response_model=CrawlResponse)
async def crawl_url(request: CrawlRequest) -> CrawlResponse:
```

Flow:
1. `httpx.get(request.url)` — fetches raw HTML
2. `html2text.HTML2Text()` converts HTML → markdown
3. Derives path: `slug = parsed.path.strip("/").replace("/", "-")` → e.g. `livekit.md` (NOT the URL)
4. Calls `ingest_document(IngestRequest(org_id=org_id, kb_slug=kb_slug, path=path, content=markdown))`
5. No `source_url` in `extra`, no links extracted

**Dedup problem for this route:** The existing dedup in `ingest_document` keys on `path` (e.g. `livekit.md`), not on the URL. The `crawled_pages` table keys on URL → gives correct URL-based dedup for this route.

---

## Database patterns

### asyncpg pool (db.py)

```python
async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        kwargs = _parse_dsn(settings.postgres_dsn)
        _pool = await asyncpg.create_pool(**kwargs, min_size=2, max_size=10)
    return _pool
```

All queries use `pool = await get_pool()`, then:
- `await pool.execute(sql, arg1, arg2, ...)` — fire-and-forget
- `await pool.fetchval(sql, arg1, ...)` — returns single value
- `await pool.fetch(sql, arg1, ...)` — returns list of rows
- `await pool.fetchrow(sql, arg1, ...)` — returns single row

No SQLAlchemy models used in knowledge_ingest. All queries are raw SQL with `$1`, `$2` positional parameters.

### Upsert pattern (ON CONFLICT)

From `pg_store.py` existing patterns — asyncpg supports:
```sql
INSERT INTO knowledge.table (col1, col2, ...)
VALUES ($1, $2, ...)
ON CONFLICT (col1) DO UPDATE SET col2 = EXCLUDED.col2
```

### Existing `knowledge.crawl_jobs` schema (006_knowledge_crawl_jobs.sql)

```sql
CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    config      JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    pages_total INTEGER NOT NULL DEFAULT 0,
    pages_done  INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  BIGINT NOT NULL,
    updated_at  BIGINT NOT NULL
);
```

### Existing `content_hash` pattern (008_knowledge_artifacts_content_hash.sql)

```sql
ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_artifacts_active_path
    ON knowledge.artifacts(org_id, kb_slug, path, belief_time_end);
```

---

## Link URL format

`result.links.get("internal", [])` from crawl4ai returns a list of dicts:
```python
[{"href": "/docs/api", "text": "API Reference"}, ...]
```

`href` values can be relative (`/docs/api`) or absolute (`https://help.voys.nl/docs/api`).
Must resolve with `urllib.parse.urljoin(base_url, href)` before storing.

In `_crawl_and_ingest_page`, `url` is the current page's absolute URL → use as base.

---

## Reference implementations

### content_hash computation (ingest.py:187)
```python
import hashlib
content_hash = hashlib.sha256(req.content.encode()).hexdigest()
```

### crawl_jobs upsert (knowledge.py:27–33)
```python
await pool.execute(
    """INSERT INTO knowledge.crawl_jobs
       (id, org_id, kb_slug, config, status, created_at, updated_at)
       VALUES ($1, $2, $3, $4, 'pending', $5, $5)""",
    job_id, req.org_id, req.kb_slug,
    json.dumps(req.model_dump()), now,
)
```

### crawl_jobs delete in pg_store.py:210
```python
await pool.execute(
    "DELETE FROM knowledge.crawl_jobs WHERE org_id = $1 AND kb_slug = $2",
    org_id, kb_slug,
)
```

---

## Risks and constraints

### Concurrency
`run_crawl_job` crawls pages sequentially (one at a time, rate-limited). No concurrent writes to `crawled_pages` within a single job. However, two jobs for the same `(org_id, kb_slug, url)` could run concurrently (though unlikely with current architecture). The `ON CONFLICT DO UPDATE` upsert handles this safely.

### Deleting crawled_pages on KB delete
`pg_store.delete_kb()` (pg_store.py:210) already deletes `knowledge.crawl_jobs` for the KB. It must also delete `crawled_pages` and `page_links`. This is in scope.

### html2text vs crawl4ai in crawl_url
The single-URL route uses html2text directly. This produces lower-quality markdown than crawl4ai (no PruningContentFilter, no nav removal). The `crawled_pages` SPEC does not require migrating crawl.py to crawl4ai — raw_markdown from html2text is sufficient for the cache. Migration is a separate decision.

### _JS_PREPARE_PAGE bug
`crawler.py:46` imports `_JS_PREPARE_PAGE` from `crawl.py` but this symbol does not exist. This causes an `ImportError` when `run_crawl_job` runs. Out of scope of this SPEC but should be fixed as a separate bug.

### raw_markdown storage size
Typical help.voys.nl pages: 5–50 KB markdown. 1000 pages = 5–50 MB. Postgres `text` has no practical limit — acceptable.

---

## Summary of insertion points

| Location | What to add |
|----------|-------------|
| `_crawl_and_ingest_page` (after `arun`, before `ingest_document`) | Compute hash, upsert `crawled_pages`, upsert `page_links`, dedup check |
| `crawl_url` (after html2text, before `ingest_document`) | Compute hash, upsert `crawled_pages`, dedup check |
| `pg_store.delete_kb()` | Add `DELETE FROM knowledge.crawled_pages` and `page_links` |
| `deploy/postgres/migrations/` | New file `011_knowledge_crawl_registry.sql` |
