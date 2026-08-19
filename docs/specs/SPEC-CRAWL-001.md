# SPEC-CRAWL-001: Crawl Wizard Improvements

**Status:** Completed
**Priority:** High
**Service:** klai-knowledge-ingest
**Primary file:** `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py`

---

## 1. Environment

- **Service:** klai-knowledge-ingest (FastAPI, asyncpg, crawl4ai REST API)
- **Database:** PostgreSQL with `knowledge` schema (asyncpg via `db.get_pool()`)
- **LLM access:** LiteLLM proxy at `LITELLM_URL` env var, model alias `klai-fast`
- **Browser engine:** Playwright (run inside shared `crawl4ai` container, accessed via REST API at `http://crawl4ai:11235`)
- **Existing config:** `knowledge_ingest/config.py` (`Settings` via pydantic-settings)
- **Existing models:** `knowledge_ingest/models.py` (`CrawlRequest`, `CrawlResponse`, `IngestRequest`)

## 2. Assumptions

- The `knowledge.artifacts` table already has a JSONB `extra` column -- no migration needed for source URL storage.
- crawl4ai runs as a shared Docker container; both klai-knowledge-ingest and klai-connector access it via REST API (`POST /crawl`). No local Playwright install needed in either service.
- The `klai-fast` LiteLLM alias is available and returns structured text responses suitable for CSS selector extraction.
- The `preview_crawl` endpoint is the primary entry point for the crawl wizard; the `crawl_url` endpoint is used for final ingestion.
- Domain selectors are scoped per org -- the same domain may have different selectors for different tenants.

## 3. User Stories

### US-1: Smart Pipeline Switching

As a knowledge base administrator, I want the crawl pipeline to automatically adjust its extraction strategy based on whether a CSS selector is present, so that user/AI-provided selectors are trusted fully without redundant nav-removal.

### US-2: Persistent Domain Selector Storage

As a knowledge base administrator, I want domain-level CSS selectors to be remembered and reused across crawl sessions, so that I do not have to re-enter selectors for domains I have crawled before.

### US-3: AI-Assisted Selector Detection

As a knowledge base administrator, I want the system to automatically detect the correct content selector when a crawl yields too little content, so that I get good results without manually inspecting the DOM.

### US-4: Source URL Stored per Artifact

As a knowledge base administrator, I want each crawled artifact to record its source URL, so that I can trace content back to its origin.

---

## 4. Requirements (EARS Format)

### R-1: Smart Pipeline Switching (Event-Driven)

**WHEN** `preview_crawl` or `crawl_url` is called with no `content_selector` (and no stored domain selector is found),
**THEN** the system **shall** use the full extraction pipeline:
- `js_code_before_wait = _JS_REMOVE_CHROME`
- `excluded_tags = ["nav", "footer", "header", "aside", "script", "style"]`
- `PruningContentFilter(threshold=0.45, threshold_type="dynamic")`

**WHEN** `preview_crawl` or `crawl_url` is called with a `content_selector` (user-provided or stored domain selector),
**THEN** the system **shall** use the trusted selector pipeline:
- `js_code_before_wait` = `None` (disabled)
- `excluded_tags` = `[]` (disabled)
- `css_selector` = the provided selector
- `PruningContentFilter(threshold=0.45, threshold_type="dynamic")` (kept)

#### Decision Table: Pipeline Configuration

| Condition | `js_code_before_wait` | `excluded_tags` | `css_selector` | `PruningContentFilter` |
|---|---|---|---|---|
| No selector | `_JS_REMOVE_CHROME` | `["nav","footer","header","aside","script","style"]` | `None` | Yes (0.45) |
| User selector present | `None` | `[]` | user value | Yes (0.45) |
| Stored AI selector | `None` | `[]` | stored value | Yes (0.45) |
| Stored user selector | `None` | `[]` | stored value | Yes (0.45) |

### R-2: Domain Selector Lookup (Event-Driven)

**WHEN** a crawl is initiated (preview or ingest),
**THEN** the system **shall** look up the domain (extracted from the URL) in `knowledge.crawl_domains` for the requesting org.

**WHEN** a stored selector is found and no user-provided selector is present,
**THEN** the system **shall** use the stored selector as `css_selector`.

**WHEN** both a user-provided selector and a stored selector exist,
**THEN** the system **shall** use the user-provided selector (user always wins).

### R-3: Domain Selector Persistence (Event-Driven)

**WHEN** a crawl completes successfully with a selector (user-provided or AI-detected) AND `word_count >= 100`,
**THEN** the system **shall** upsert the selector into `knowledge.crawl_domains` with:
- `domain` = parsed domain from URL
- `org_id` = requesting org
- `css_selector` = the selector used
- `selector_source` = `'user'` or `'ai'`
- `updated_at` = current timestamp

**WHEN** a user-provided selector is persisted for a domain that already has an AI-stored selector,
**THEN** the system **shall** overwrite the AI selector with the user selector.

### R-4: AI-Assisted Selector Detection (Event-Driven)

**WHEN** a crawl completes with `word_count < 100` AND no user-provided selector was used,
**THEN** the system **shall**:
1. Extract a DOM summary: top 25 elements by word count, each with `{tag, id, className, wordCount, selector}`
2. Send the DOM summary to `klai-fast` via `httpx` to the LiteLLM proxy with the prompt: "Given this DOM summary of a webpage, identify the single CSS selector that contains the main article content (not nav, sidebar, or footer). Return only the selector string."
3. Re-crawl with the returned selector using the trusted pipeline (R-1)
4. If the re-crawl yields `word_count >= 100`: store the AI selector (R-3) and return the re-crawl result
5. If the re-crawl yields `word_count < 100`: return the original result with a `"low_word_count"` warning, do not store

### R-5: Source URL in Artifact Extra (Ubiquitous)

The system **shall** include `source_url` in the `extra` JSON field of `knowledge.artifacts` for every crawled document.

### R-6: Selector Source Priority (Unwanted)

The system **shall not** use an AI-detected selector when a user-provided selector is present.

The system **shall not** store an AI-detected selector that yields fewer than 100 words.

---

## 5. Database Migration

### New Table: `knowledge.crawl_domains`

```sql
CREATE TABLE IF NOT EXISTS knowledge.crawl_domains (
    domain      TEXT        NOT NULL,
    org_id      TEXT        NOT NULL,
    css_selector TEXT       NOT NULL,
    selector_source TEXT    NOT NULL CHECK (selector_source IN ('user', 'ai')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, org_id)
);

COMMENT ON TABLE knowledge.crawl_domains IS
    'Persistent CSS selectors per domain per org, used by the crawl wizard.';
```

### No migration needed for `source_url`

The `knowledge.artifacts.extra` column is JSONB. The `source_url` field is added at insert time via the existing `extra` dict parameter in `pg_store.create_artifact()`.

---

## 6. Implementation Plan

### Milestone 1 (Primary Goal): Smart Pipeline Switching

**Files to modify:**
- `knowledge_ingest/routes/crawl.py` -- refactor `CrawlerRunConfig` construction into a helper function that takes a `selector: str | None` parameter and returns the appropriate config

**Approach:**
1. Extract config construction into `_build_crawl_config(selector: str | None) -> CrawlerRunConfig`
2. When `selector` is `None`: use full pipeline (current behavior)
3. When `selector` is provided: disable `excluded_tags` and `js_code_before_wait`, pass selector as `css_selector`
4. Update both `preview_crawl` and `crawl_url` to use the helper

### Milestone 2 (Primary Goal): Domain Selector Storage

**Files to create/modify:**
- `knowledge_ingest/domain_selectors.py` (new) -- async functions for CRUD on `crawl_domains`
- `knowledge_ingest/routes/crawl.py` -- integrate lookup and persistence
- Migration SQL file for `knowledge.crawl_domains` table

**Approach:**
1. Create `domain_selectors.py` with:
   - `async def get_domain_selector(domain: str, org_id: str) -> tuple[str, str] | None` -- returns `(css_selector, selector_source)` or `None`
   - `async def upsert_domain_selector(domain: str, org_id: str, css_selector: str, selector_source: str) -> None`
2. In `preview_crawl`: before crawling, look up domain selector; after successful crawl, persist selector
3. In `crawl_url`: same lookup logic (selector from request body takes precedence)
4. Add `org_id` to `CrawlPreviewRequest` model (required for domain lookup)

### Milestone 3 (Secondary Goal): AI-Assisted Selector Detection

**Files to create/modify:**
- `knowledge_ingest/selector_ai.py` (new) -- DOM summary extraction and LLM call
- `knowledge_ingest/routes/crawl.py` -- integrate AI fallback

**Approach:**
1. Create `_extract_dom_summary(page) -> list[dict]` using Playwright's `page.evaluate()` to run JS that:
   - Finds all elements with `innerText`
   - Ranks by word count
   - Returns top 25 with `{tag, id, className, wordCount, selector}` (where `selector` is a unique CSS path)
2. Create `_detect_selector_via_llm(dom_summary: list[dict]) -> str | None` that:
   - Calls LiteLLM proxy (`settings.litellm_url + "/v1/chat/completions"`) with model `klai-fast`
   - Parses the response for a CSS selector string
   - Returns `None` on failure
3. In `preview_crawl`: after initial crawl, if `word_count < 100` and no user selector, trigger AI detection
4. Re-crawl with AI selector using trusted pipeline
5. Store if successful (>= 100 words)

### Milestone 4 (Secondary Goal): Source URL in Artifacts

**Files to modify:**
- `knowledge_ingest/routes/crawl.py` -- pass `source_url` in `extra` dict when calling `ingest_document`

**Approach:**
1. In `crawl_url`: add `extra={"source_url": request.url}` to the `IngestRequest`
2. The `IngestRequest.extra` field already exists and is passed through to `pg_store.create_artifact()`

---

## 7. Technical Notes

### DOM Summary Extraction JS

```javascript
(() => {
  const els = [...document.body.querySelectorAll('*')]
    .filter(el => el.innerText && el.children.length < 5)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: el.className || null,
      wordCount: el.innerText.trim().split(/\s+/).length,
      selector: el.id ? `#${el.id}` :
        el.className ? `${el.tagName.toLowerCase()}.${el.className.trim().split(/\s+/).join('.')}` :
        el.tagName.toLowerCase()
    }))
    .sort((a, b) => b.wordCount - a.wordCount)
    .slice(0, 25);
  return els;
})()
```

### LLM Prompt for Selector Detection

```
Given this DOM summary of a webpage (sorted by word count descending), identify the single CSS selector that contains the main article/content body. Exclude navigation, sidebar, footer, and header elements. Return ONLY the CSS selector string, nothing else.

DOM Summary:
{json_summary}
```

### CrawlPreviewRequest Model Change

```python
class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None
    org_id: str  # NEW: required for domain selector lookup
```

**Breaking change note:** Adding `org_id` as required makes this a breaking change for existing callers. If the frontend does not yet send `org_id`, make it optional with a default of `""` and skip domain lookup when empty.

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| AI selector detection returns invalid CSS | Re-crawl fails or returns empty content | Wrap re-crawl in try/except; return original result with warning on failure |
| crawl4ai does not expose Playwright page object | Cannot extract DOM summary | Use crawl4ai's `js_code` parameter to inject DOM extraction JS and capture result via `result.extracted_content` |
| LiteLLM proxy unavailable | AI selector detection fails | Catch httpx errors; return original crawl result without AI fallback |
| Domain selector table grows unbounded | Storage concern | Low risk -- one row per (domain, org_id); consider TTL cleanup later |
| Adding `org_id` to preview request breaks frontend | 400 errors from frontend | Make `org_id` optional with empty string default; skip domain lookup when missing |

---

## 9. Acceptance Criteria (Given-When-Then)

### AC-1: Full pipeline when no selector

```
Given a URL with no user-provided selector and no stored domain selector
When preview_crawl is called
Then CrawlerRunConfig uses _JS_REMOVE_CHROME, excluded_tags=["nav",...], and PruningContentFilter
```

### AC-2: Trusted pipeline with user selector

```
Given a URL with a user-provided content_selector "article.main-content"
When preview_crawl is called
Then CrawlerRunConfig has js_code_before_wait=None, excluded_tags=[], css_selector="article.main-content", and PruningContentFilter
```

### AC-3: Stored domain selector reuse

```
Given domain "help.example.com" has a stored selector "#content" for org "org-123"
And no user-provided selector
When preview_crawl is called for "https://help.example.com/page" with org_id "org-123"
Then the stored selector "#content" is used with the trusted pipeline
```

### AC-4: User selector overrides stored selector

```
Given domain "help.example.com" has a stored selector "#content" for org "org-123"
And user provides content_selector ".custom-area"
When preview_crawl is called
Then ".custom-area" is used (not "#content")
```

### AC-5: Domain selector persistence after successful crawl

```
Given a crawl with selector ".article-body" yields word_count >= 100
When the crawl completes
Then a row is upserted in knowledge.crawl_domains with domain, org_id, css_selector=".article-body", and selector_source
```

### AC-6: AI selector detection on low word count

```
Given a crawl with no selector yields word_count = 42
When AI selector detection runs
And the AI returns selector "#main-article"
And re-crawl with "#main-article" yields word_count = 350
Then the response contains the re-crawled content (350 words)
And "#main-article" is stored in crawl_domains with selector_source='ai'
```

### AC-7: AI selector not stored when re-crawl also fails

```
Given a crawl with no selector yields word_count = 42
When AI selector detection runs
And re-crawl with the AI selector yields word_count = 30
Then the original result (42 words) is returned with warning "low_word_count"
And no selector is stored in crawl_domains
```

### AC-8: Source URL in artifact extra

```
Given a URL "https://docs.example.com/guide" is crawled via crawl_url
When the artifact is created
Then the artifact's extra JSON contains {"source_url": "https://docs.example.com/guide"}
```

### AC-9: Org isolation for domain selectors

```
Given domain "example.com" has selector "#blog" for org "org-A"
And org "org-B" has no stored selector for "example.com"
When org-B crawls "https://example.com/page"
Then no stored selector is used (org-B gets the full pipeline)
```

---

## 10. Definition of Done

- [ ] Smart pipeline switching implemented and tested (R-1)
- [ ] `knowledge.crawl_domains` table created via migration (R-2, R-3)
- [ ] Domain selector lookup integrated into preview and crawl endpoints (R-2)
- [ ] Domain selector persistence after successful crawls (R-3)
- [ ] AI-assisted selector detection with LLM call and re-crawl (R-4)
- [ ] `source_url` populated in artifact `extra` for crawled documents (R-5)
- [ ] User selector always overrides stored/AI selectors (R-6)
- [ ] No OpenAI/Anthropic model names in code -- only `klai-fast` alias
- [ ] Error handling: LLM failure and invalid CSS selector gracefully handled
- [ ] `CrawlPreviewRequest.org_id` added (optional for backwards compatibility)

---

## 11. Amendment: shared host pacing and safe domain-rate persistence

**Added and implemented:** 2026-08-19

### 11.1 Corrected identity contract

The persisted `knowledge.crawl_domains` key and the live crawl-pacing key are
deliberately different concepts:

- `extract_domain(url)` remains the exact normalized storage key used for both
  selectors and the existing org-scoped domain-rate state. It normalizes case,
  IDNA, default ports, and trailing dots, but does **not** collapse apex and
  `www` hosts. Changing this contract would silently merge selector rows and can
  conflict with existing `(domain, org_id)` primary keys.
- `crawl_gate_key(url)` is the process-local pacing identity. It applies the
  same hostname and port normalization and then removes exactly one leading
  `www.` label. It does not use a registrable-domain/Public-Suffix-List rule and
  does not merge arbitrary subdomains.
- The live host gate is shared across orgs because concurrent tenants still
  consume the same external host capacity. Persisted selectors and rate state
  remain org-scoped.

### 11.2 Shared host gate

**WHEN** two or more crawl operations target the same `crawl_gate_key`,
**THEN** all target requests from those operations **shall** share one
process-wide weighted pacing schedule.

The pacing unit is target URLs, not Crawl4AI HTTP calls. A bulk request carrying
`N` URLs consumes weight `N`. The effective rate is the lowest rate requested
by any active session for the host. A rate reduction during a crawl applies to
all subsequent grants and also recalculates subsequent burst sizes.

The schedule shall be fair between waiters, cancellation-safe, and removed from
the registry when its final active session and waiter leave. Apex and `www`
variants share a gate; unrelated hosts do not.

The gate is intentionally in memory. This is valid only while the FastAPI app
and Procrastinate workers run in one Python process and knowledge-ingest has one
replica. Multiple Uvicorn workers or replicas require a distributed limiter
(for example Redis/GCRA) before rollout.

### 11.3 Global Crawl4AI request cap

Every knowledge-ingest `POST /crawl` request, including seed, bulk, recovery,
preview, and DOM-summary requests, shall pass through one process-wide bounded
semaphore. Its size is configurable and defaults to `1` so the service does not
fill the shared Crawl4AI container with concurrent requests; klai-connector has
its own independent caller and is not governed by this semaphore.

The request cap is a service-side safety boundary, not a hard reservation of
Crawl4AI page slots. The pinned Crawl4AI pool permits 40 concurrent pages and a
single payload can contain multiple URLs.

### 11.4 Conflict-safe domain-rate writes

Domain-rate persistence shall use compare-and-swap semantics over
`rate_limit`, `clean_streak`, and `last_congestion_at` rather than an
unconditional stale-state overwrite.

- A write succeeds normally only when the stored state still equals the state
  from which the proposal was computed (`NULL` compared null-safely).
- On conflict, congestion merges atomically toward the lowest of the current
  and proposed rates, resets the clean streak, and preserves the newest
  congestion timestamp.
- A conflicting recovery or read-time decay never overwrites changed state; it
  is deferred until a later crawl.
- Concurrent congestion proposals derived from the same state produce one
  lowering, not an accidental double-halving.

This makes congestion monotonic over concurrent recovery: whichever operation
reaches PostgreSQL first, their crossing ends at the lower proposed speed.

### 11.5 Amendment acceptance criteria

- **AC-10:** The two same-host crawls reproducing the 2026-08-18 09:27 incident
  do not exceed their shared aggregate URL cadence.
- **AC-11:** Crawls on different hosts use independent host schedules.
- **AC-12:** `www.example.com` and `example.com` share live pacing while their
  `extract_domain()` storage keys remain distinct.
- **AC-13:** The configured global cap bounds concurrent Crawl4AI POSTs across
  different domains and across seed/bulk/recovery paths.
- **AC-14:** Concurrent lowering and recovery writes finish at the lower
  proposal in both execution orders; decay cannot erase newer congestion.
- **AC-15:** Existing circuit-breaker, cancellation, slowdown, retry, and
  self-healing behavior remains covered by the full knowledge-ingest suite.
