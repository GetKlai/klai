# SPEC-CRAWL-002: Two-Phase Web Crawler — Discovery + Extraction Split

**Status:** Planned
**Priority:** High
**Created:** 2026-04-16
**Revised:** 2026-04-16

---

## Problem

The current `WebCrawlerAdapter.list_documents()` applies `content_selector` (CSS selector) to
every page during the crawl, including the homepage used for BFS discovery. When the homepage
does not contain the selector element — which is the common case for wiki-style sites where
`nav`, `article`, and `.tab-structure` only appear on interior content pages — `PruningContentFilter`
and `wait_for` see no matching content, the crawl returns 0 pages, and the KB is empty.

Root cause: discovery (finding URLs) and extraction (reading content) share the same
`CrawlerRunConfig`. The `content_selector` is meant for extraction only.

Additionally, `_start_crawl()` and `_poll_task()` exist in the adapter as dead code. They submit
a BFS job via `POST /crawl/job` but are never called from `list_documents()`. The current
implementation only uses `POST /crawl` (synchronous) on the homepage URL + sitemap supplement,
which means BFS multi-level link-following never fires.

## Goal

Split the crawl into two phases:

1. **Discovery phase** — BFS via `/crawl/job` from `base_url`, restricted to `path_prefix`,
   no `content_selector`, collects all reachable URLs.
2. **Extraction phase** — batch re-crawl of discovered URLs via `/crawl` (sync), with
   `content_selector` applied. Skipped when no `content_selector` is configured; in that case
   the discovery results are used directly.

This makes `content_selector` an extraction-only concern and ensures BFS finds all pages
regardless of whether the homepage matches the selector.

---

## Success Criteria

- BFS discovery finds all linked pages within `base_url` + `path_prefix`, regardless of `content_selector`
- When `content_selector` is set, content is extracted only from elements matching the selector
- When `content_selector` is not set, discovery results are used directly (no redundant re-crawl)
- Cookies (authentication) are injected in both phases
- `path_prefix` filter restricts discovery to URLs that start with `base_url` + `path_prefix`
- No frontend changes required
- No changes to `klai-knowledge-ingest` preview endpoint (single-page, already correct)
- No new database tables or migrations

---

## Environment

- **klai-connector:** Python 3.12, FastAPI, httpx, structlog
- **Crawl4AI:** REST API at `http://crawl4ai:11235`, version 0.8.6
  - `POST /crawl` — synchronous batch crawl (up to 100 URLs per request)
  - `POST /crawl/job` — async BFS job submission, returns `task_id`
  - `GET /crawl/job/{task_id}` — poll for BFS job status/result
  - Hooks enabled via `CRAWL4AI_HOOKS_ENABLED=true` (set in docker-compose.yml)
- **Auth:** Cookies injected via `on_page_context_created` hook (`_build_cookie_hooks()`)

---

## Requirements

### Phase 1: Discovery (BFS)

**REQ-1: New `_build_discovery_params()` method**
The adapter SHALL expose a `_build_discovery_params(config: dict[str, Any]) -> dict[str, Any]`
method that returns a `CrawlerRunConfig`-compatible params dict for BFS discovery.
Discovery params differ from extraction params:
- No `css_selector` / `content_selector`
- No `PruningContentFilter` (BFS only needs links, not clean markdown)
- `word_count_threshold: 0` to avoid discarding link-heavy pages with little visible text
- `js_code_before_wait` = `_JS_REMOVE_CHROME` (strip nav chrome to help wait_for)
- `excluded_tags`: `["nav", "footer", "header", "aside", "script", "style"]`
- `page_timeout: 30000`
- `cache_mode: "bypass"`

**REQ-2: Restore `_start_crawl()` as the BFS engine**
`_start_crawl(config, crawl_params, cookies)` SHALL be updated to:
- Accept an explicit `crawl_params: dict[str, Any]` parameter (built by caller)
- Accept `cookies: list[dict[str, Any]] | None = None`
- Build `deep_crawl_strategy` with `BFSDeepCrawlStrategy`:
  - `max_depth` from config (default 3)
  - `max_pages` from config (default 200, capped at 2000)
  - `filter_chain` with `URLPatternFilter` when `path_prefix` is set
- Inject cookies via `payload["hooks"] = _build_cookie_hooks(cookies)` when cookies are present
- Submit `POST /crawl/job` and return `task_id`

**REQ-3: URLPatternFilter pattern construction**
WHEN `path_prefix` is configured, the `URLPatternFilter` pattern SHALL be constructed as:
`base_url.rstrip("/") + "/" + path_prefix.lstrip("/")` so that only URLs under that path are followed.
WHEN `path_prefix` is empty or None, no `filter_chain` is added (crawl entire domain).

**REQ-4: `_poll_task()` — already correct, no changes needed**
`_poll_task()` SHALL remain unchanged. It polls `GET /crawl/job/{task_id}` until `status == "completed"` or raises `CrawlJobPendingError` after `_MAX_POLL_SECONDS`.

**REQ-5: Extract discovered URLs from BFS result**
After `_poll_task()` returns, the adapter SHALL extract all crawled URLs from the result via
`_process_results()` and return them as `DocumentRef` objects with their markdown content
already populated in `cache`. These are the Phase 1 results.

### Phase 2: Extraction (re-crawl with selector)

**REQ-6: Extraction phase is skipped when no `content_selector`**
WHEN `content_selector` is empty or None, the adapter SHALL use Phase 1 results directly.
No second crawl is performed.

**REQ-7: Extraction phase re-crawls with `content_selector`**
WHEN `content_selector` is set:
- Take all URLs discovered in Phase 1
- Re-crawl them in batches via `_crawl_pages_sync()` with `_build_page_crawl_params(config)`
  (which includes `css_selector = content_selector`)
- The Phase 1 `DocumentRef` objects are **replaced** by Phase 2 results (Phase 2 has correct content)
- Cookies are passed to `_crawl_pages_sync()` in Phase 2

**REQ-8: Cache consistency after Phase 2**
After Phase 2 completes, the `cache` dict SHALL reflect Phase 2 content for all re-crawled URLs.
URLs that Phase 2 returns empty content for (selector matched nothing) are dropped from results.

### `list_documents()` Rewrite

**REQ-9: `list_documents()` orchestrates two phases**
`list_documents()` SHALL be rewritten to:

```
Phase 1: BFS discovery
  task_id = await _start_crawl(config, discovery_params, cookies)
  result  = await _poll_task(task_id)
  refs    = _process_results(result, cache, base_url)

Phase 2: extraction (only if content_selector set)
  urls    = [ref.ref for ref in refs]
  refs    = await _crawl_pages_sync(urls, page_params, cache, base_url, cookies)

Sitemap supplement (unchanged, runs after both phases):
  fill remaining slots from sitemap URLs not yet in cache
```

**REQ-10: Sitemap supplement unchanged**
The sitemap supplement logic (Phase 3 in practice) remains: fetch `sitemap.xml`, supplement with
URLs not yet in `cache`, up to `max_pages - len(refs)` remaining slots. Uses `page_params`
(with selector if set). No behavioural change from current implementation.

**REQ-11: Logging**
`list_documents()` SHALL log:
- `info` at start: `base_url`, `max_pages`, `authenticated` (bool), `has_selector` (bool)
- `info` after Phase 1: number of URLs discovered
- `info` after Phase 2 (if run): number of pages with content after extraction
- `info` at end: total pages returned

### Cookie Handling

**REQ-12: Cookies in both phases**
`cookies` from `config.get("cookies")` SHALL be passed to:
- `_start_crawl()` (Phase 1, BFS)
- `_crawl_pages_sync()` (Phase 2, extraction; and sitemap supplement)

---

## Out of Scope

- Changes to the knowledge-ingest preview endpoint (single-page crawl, already correct)
- Frontend UI changes (connector config fields remain unchanged)
- Database migrations
- `CrawlJobPendingError` retry logic (already implemented, unchanged)
- Incremental / delta sync (future SPEC)

---

## Acceptance Criteria

### AC-1: BFS finds pages when homepage lacks selector
**Given** a connector with `base_url = "https://wiki.example.com"` and
`content_selector = ".tab-structure"` and `path_prefix = "/en"`
**When** `list_documents()` runs
**Then** Phase 1 BFS discovers interior pages (e.g. `/en/crm-software/act`)
**And** Phase 2 extracts content using `.tab-structure` from those interior pages
**And** the returned `DocumentRef` list is non-empty

### AC-2: No `content_selector` → single phase
**Given** a connector with no `content_selector`
**When** `list_documents()` runs
**Then** only Phase 1 BFS runs — `_crawl_pages_sync()` is NOT called for extraction
**And** the BFS markdown is used as document content directly

### AC-3: `path_prefix` restricts BFS
**Given** `base_url = "https://wiki.example.com"` and `path_prefix = "/en"`
**When** BFS runs
**Then** `URLPatternFilter` pattern is `"https://wiki.example.com/en"`
**And** no URLs outside `/en` are followed

### AC-4: Cookies injected in Phase 1
**Given** a connector with cookies configured
**When** `_start_crawl()` builds the BFS payload
**Then** `payload["hooks"]` contains the `on_page_context_created` hook with the cookies JSON

### AC-5: Cookies injected in Phase 2
**Given** a connector with cookies and `content_selector`
**When** Phase 2 calls `_crawl_pages_sync()`
**Then** the `cookies` parameter is passed through and the hook is included in the batch payload

### AC-6: Phase 2 replaces Phase 1 content
**Given** Phase 1 found 50 URLs with raw BFS markdown
**And** `content_selector = ".tab-structure"` is set
**When** Phase 2 re-crawls those URLs with the selector
**Then** the final `refs` list reflects Phase 2 content, not Phase 1 BFS markdown
**And** URLs where the selector matched nothing are absent from the final list

### AC-7: Sitemap supplement still works
**Given** BFS finds 30 pages and `max_pages = 100`
**When** sitemap.xml contains 50 additional URLs not in the BFS result
**Then** up to 70 sitemap URLs are crawled in supplement batches
**And** `cookies` and `content_selector` (if set) are applied to supplement batches

### AC-8: `_start_crawl()` dead code is removed
**Given** the current `_start_crawl()` and `_poll_task()` implementations
**When** the refactor is complete
**Then** `_start_crawl()` is called from `list_documents()` (no longer dead code)
**And** no unreachable code paths remain in the adapter

---

## Implementation Notes

### Files to Change

**One file only:**
- `klai-connector/app/adapters/webcrawler.py`

### Method Map (before → after)

| Method | Before | After |
|---|---|---|
| `_build_discovery_params(config)` | does not exist | NEW — BFS params, no selector |
| `_build_page_crawl_params(config)` | extraction params (unchanged) | unchanged |
| `_start_crawl(config)` | dead code, no `crawl_params` arg | RESTORED — accepts `crawl_params`, `cookies` |
| `_poll_task(task_id)` | exists, correct | unchanged |
| `_crawl_pages_sync(...)` | exists, correct | unchanged |
| `_process_results(...)` | exists, correct | unchanged |
| `list_documents(...)` | homepage sync + sitemap | Phase 1 (BFS) + Phase 2 (extraction if selector) + sitemap |

### `_start_crawl()` signature change

```python
async def _start_crawl(
    self,
    config: dict[str, Any],
    crawl_params: dict[str, Any],
    cookies: list[dict[str, Any]] | None = None,
) -> str:
```

`crawl_params` is built by the caller (`_build_discovery_params`). `_start_crawl` adds
`deep_crawl_strategy` to it and submits the job.

### `list_documents()` skeleton

```python
async def list_documents(self, connector, cursor_context=None):
    connector_id = str(connector.connector_id)
    cache: dict[str, str] = {}
    self._crawl_cache[connector_id] = cache
    config = connector.config
    base_url: str = config.get("base_url", "")
    max_pages: int = min(config.get("max_pages", 200), 2000)
    content_selector: str | None = config.get("content_selector") or None
    cookies: list[dict] | None = config.get("cookies") or None

    logger.info("Starting crawl", base_url=base_url, max_pages=max_pages,
                authenticated=bool(cookies), has_selector=bool(content_selector))

    # Phase 1: BFS discovery (no selector)
    discovery_params = self._build_discovery_params(config)
    task_id = await self._start_crawl(config, discovery_params, cookies)
    result = await self._poll_task(task_id)
    refs = self._process_results(result, cache, base_url=base_url)
    logger.info("BFS discovery complete", urls_found=len(refs))

    # Phase 2: extraction re-crawl (only when content_selector is set)
    if content_selector and refs:
        page_params = self._build_page_crawl_params(config)
        urls = [ref.ref for ref in refs]
        refs = await self._crawl_pages_sync(urls, page_params, cache, base_url=base_url, cookies=cookies)
        logger.info("Extraction complete", pages_with_content=len(refs))

    # Phase 3: sitemap supplement (unchanged)
    if len(refs) < max_pages:
        ...  # existing sitemap logic, uses page_params (with selector if set)

    logger.info("Crawl complete", total_pages=len(refs), base_url=base_url)
    return refs
```

### URLPatternFilter pattern

```python
if allowed_path_prefix:
    pattern = base_url.rstrip("/") + "/" + allowed_path_prefix.lstrip("/")
    deep_crawl_params["filter_chain"] = [
        {"type": "URLPatternFilter", "params": {"patterns": [pattern]}},
    ]
```

### `_build_discovery_params()` sketch

```python
def _build_discovery_params(self, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "cache_mode": "bypass",
        "word_count_threshold": 0,
        "wait_for": "js:() => document.body.innerText.trim().split(/\\s+/).length > 10",
        "js_code_before_wait": _JS_REMOVE_CHROME,
        "excluded_tags": ["nav", "footer", "header", "aside", "script", "style"],
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "page_timeout": 30000,
        "markdown_generator": {
            "type": "DefaultMarkdownGenerator",
            "params": {
                "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
            },
        },
    }
```

No `PruningContentFilter` in discovery — we want all links, not filtered prose.
No `css_selector` — the selector is only for extraction.

---

## Test Plan

| Test | What to verify |
|---|---|
| `test_list_documents_with_selector_calls_two_phases` | `_start_crawl` + `_poll_task` called once; `_crawl_pages_sync` called for extraction |
| `test_list_documents_without_selector_skips_extraction` | `_crawl_pages_sync` NOT called for extraction when no selector |
| `test_build_discovery_params_no_selector` | `css_selector` key absent from discovery params |
| `test_start_crawl_injects_path_prefix_filter` | `URLPatternFilter` pattern = base_url + "/" + path_prefix |
| `test_start_crawl_no_path_prefix_no_filter` | no `filter_chain` when path_prefix empty |
| `test_start_crawl_injects_cookies` | `payload["hooks"]` present when cookies given |
| `test_phase2_replaces_phase1_content` | final refs reflect extraction content not BFS markdown |
| `test_sitemap_supplement_still_runs` | sitemap URLs added after phase 2 up to max_pages |
