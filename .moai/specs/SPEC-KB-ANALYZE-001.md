# SPEC-KB-ANALYZE-001: Web Crawler Preview Wizard + Content Pipeline Fix

**Status:** Planned
**Priority:** High
**Created:** 2026-03-31
**Revised:** 2026-03-31

---

## Goal

Two related improvements shipped together:

1. **Fix the content pipeline** — three bugs in `klai-knowledge-ingest` that prevent `content_selector` from being respected and cause crawled content to come from the wrong field.
2. **Add a crawl preview wizard** — before starting a crawl, users can preview exactly what `PruningContentFilter` will extract from the target URL. No LLM, no user input needed. The preview shows `fit_markdown` — the exact content that will go into the KB.

## Success Criteria

- `content_selector` is passed through the full pipeline (API → Procrastinate task → crawl job)
- Crawled pages use `PruningContentFilter` and `result.markdown.fit_markdown` for content extraction
- Users can preview content before starting a crawl from both create and edit forms
- Preview shows the filtered markdown and a word count
- `content_selector` stays as an advanced override; preview works without it
- All UI strings are available in NL and EN via Paraglide i18n
- No new database tables or migrations

---

## Environment

- **knowledge-ingest backend:** Python 3.12, FastAPI, crawl4ai, structlog, Procrastinate
- **Portal backend:** FastAPI, Python 3.12, httpx, structlog
- **Frontend:** React 19, Vite, TanStack Router, TanStack Query, Paraglide i18n
- **Existing config:** `settings.knowledge_ingest_url` and `settings.knowledge_ingest_secret` in portal `app/core/config.py`

## Assumptions

- crawl4ai is already installed in knowledge-ingest (`PruningContentFilter` and `DefaultMarkdownGenerator` are available)
- `content_selector` field already exists in the frontend webcrawler config forms (both create and edit)
- Target pages are publicly accessible
- `fit_markdown` is always available after crawl; fall back to `raw_markdown` if empty

---

## Requirements

### Bug Fixes — knowledge-ingest

**REQ-1: PruningContentFilter in CrawlerRunConfig**
WHEN a crawl job runs, the system SHALL use `DefaultMarkdownGenerator` with `PruningContentFilter(threshold=0.45, threshold_type="dynamic")` as the `content_filter`. This replaces the current configuration that uses no content filter.

```python
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    word_count_threshold=10,
    excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
    markdown_generator=DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
    ),
    css_selector=content_selector or None,
)
```

**REQ-2: Use fit_markdown for content**
WHEN extracting text from a crawl result, the system SHALL use `result.markdown.fit_markdown` as the primary source. IF `fit_markdown` is empty, the system SHALL fall back to `result.markdown.raw_markdown`. The system SHALL NOT use `result.markdown` (string coercion) or `result.cleaned_html`.

**REQ-3: content_selector in run_crawl_job**
The `run_crawl_job` function in `crawler.py` SHALL accept a `content_selector: str | None = None` parameter and pass it as `css_selector` to `CrawlerRunConfig`.

**REQ-4: content_selector in Procrastinate task**
The `run_crawl` Procrastinate task in `crawl_tasks.py` SHALL accept a `content_selector: str | None = None` parameter and forward it to `run_crawl_job`.

**REQ-5: content_selector in start_crawl endpoint**
The `start_crawl` endpoint in `routes/knowledge.py` SHALL read `content_selector` from `BulkCrawlRequest` (field already exists or SHALL be added) and pass it to `proc_app.run_crawl.defer_async(...)`.

### Preview Endpoint — knowledge-ingest

**REQ-6: POST /ingest/v1/crawl/preview endpoint**
The system SHALL expose a `POST /ingest/v1/crawl/preview` endpoint in `routes/crawl.py` (alongside the existing single-page crawl endpoint). The endpoint SHALL:
- Accept `{ "url": "<target_url>", "content_selector": "<css_selector_or_null>" }`
- Fetch the page using `AsyncWebCrawler` with `PruningContentFilter` (same config as REQ-1)
- Return `{ "fit_markdown": "<filtered_content>", "word_count": <int>, "url": "<url>" }`
- Use a 15-second timeout for the crawl
- Return HTTP 200 in all cases; on error return `{ "fit_markdown": "", "word_count": 0, "url": "<url>" }`

**REQ-7: Preview endpoint authentication**
The preview endpoint SHALL require the `X-Ingest-Secret` header matching `settings.ingest_secret`, consistent with other endpoints in `routes/crawl.py`.

**REQ-8: Preview endpoint logging**
The endpoint SHALL log the URL at `info` level and any errors at `warning` level using structlog.

### Portal Backend Proxy

**REQ-9: preview_crawl function in knowledge_ingest_client.py**
The `KnowledgeIngestClient` in `klai-portal/backend/app/services/knowledge_ingest_client.py` SHALL gain a `preview_crawl(url: str, content_selector: str | None) -> dict` async method that calls `POST {settings.knowledge_ingest_url}/ingest/v1/crawl/preview` with the ingest secret header. On any error, it SHALL return `{ "fit_markdown": "", "word_count": 0, "url": url }`.

**REQ-10: POST /api/app/knowledge-bases/{kb_slug}/connectors/crawl-preview endpoint**
`klai-portal/backend/app/api/app_knowledge_bases.py` SHALL expose a `POST /api/app/knowledge-bases/{kb_slug}/connectors/crawl-preview` endpoint that:
- Accepts `{ "url": "<target_url>", "content_selector": "<optional>" }`
- Requires bearer token + org authentication (same pattern as other endpoints in the file)
- Calls `knowledge_ingest_client.preview_crawl(...)` and returns the result unchanged
- Requires the `owner` role

### Frontend — Preview Wizard

**REQ-11: Preview button on create form**
WHEN the user has entered a non-empty `base_url` in the web crawler create form, the system SHALL display a "Voorvertoning" button (variant `outline`, size `sm`) after the `base_url` input. The button SHALL be disabled when `base_url` is empty.

**REQ-12: Preview button on edit form**
WHEN the user is editing an existing web crawler connector and `base_url` is non-empty, the system SHALL display the same "Voorvertoning" button.

**REQ-13: Loading state**
WHEN the user clicks the preview button, the button SHALL show a spinner and "Laden..." label and be disabled until the response arrives.

**REQ-14: Preview result display**
WHEN the preview returns with non-empty `fit_markdown`, the system SHALL display a preview panel below the `content_selector` field showing:
- A header: "Voorvertoning van KB-inhoud"
- The word count: "~{word_count} woorden"
- The `fit_markdown` content in a scrollable `<pre>` block (max height `12rem`, overflow-y auto)

**REQ-15: Empty preview**
WHEN the preview returns with empty `fit_markdown`, the system SHALL display a message: "Geen inhoud gevonden. Probeer een andere URL of voeg een content selector toe."

**REQ-16: content_selector as advanced override**
The `content_selector` field SHALL remain visible in the form as an advanced override. When the user provides a value and triggers the preview, it SHALL be sent as `content_selector` in the preview request. No label change is required.

**REQ-17: i18n**
All new UI strings SHALL be added to both `messages/en.json` and `messages/nl.json` using Paraglide. The following keys SHALL be added:

| Key | NL | EN |
|---|---|---|
| `admin_connectors_webcrawler_preview_button` | Voorvertoning | Preview content |
| `admin_connectors_webcrawler_preview_loading` | Laden... | Loading... |
| `admin_connectors_webcrawler_preview_title` | Voorvertoning van KB-inhoud | KB content preview |
| `admin_connectors_webcrawler_preview_word_count` | ~{count} woorden | ~{count} words |
| `admin_connectors_webcrawler_preview_empty` | Geen inhoud gevonden. Probeer een andere URL of voeg een content selector toe. | No content found. Try a different URL or add a content selector. |

---

## Out of Scope

- Crawling pages behind authentication
- Caching preview results
- Automatic re-preview when URL changes
- Showing raw HTML or the original unfiltered markdown
- Any LLM calls in this SPEC

---

## Acceptance Criteria

### AC-1: content_selector passes through the pipeline
**Given** a web crawler connector has `content_selector = "main article"`
**When** a crawl job runs
**Then** `CrawlerRunConfig` receives `css_selector="main article"`
**And** the crawl result uses `fit_markdown` not `cleaned_html`

### AC-2: PruningContentFilter is active
**Given** a crawl job runs without `content_selector`
**When** the page is fetched
**Then** `CrawlerRunConfig.markdown_generator` is a `DefaultMarkdownGenerator` with `PruningContentFilter` attached
**And** `result.markdown.fit_markdown` is used as the page text

### AC-3: Preview endpoint returns content
**Given** a POST to `/ingest/v1/crawl/preview` with `{ "url": "https://docs.example.com" }`
**When** the page is fetched and filtered
**Then** the response contains non-empty `fit_markdown` and a positive `word_count`

### AC-4: Preview endpoint handles errors gracefully
**Given** a POST to `/ingest/v1/crawl/preview` with an unreachable URL
**When** the crawl times out or fails
**Then** HTTP 200 is returned with `{ "fit_markdown": "", "word_count": 0, "url": "<url>" }`

### AC-5: Portal proxy endpoint works
**Given** the user is authenticated and has the `owner` role
**When** a POST to `/api/app/knowledge-bases/{kb_slug}/connectors/crawl-preview` is made
**Then** the response mirrors what knowledge-ingest returned

### AC-6: Preview button disabled when URL empty
**Given** `base_url` is empty in the create or edit form
**Then** the "Voorvertoning" button is disabled

### AC-7: Preview shows fit_markdown content
**Given** the user enters a valid URL and clicks "Voorvertoning"
**When** the preview loads
**Then** a panel appears showing the word count and the `fit_markdown` in a scrollable block

### AC-8: Empty preview shows message
**Given** the URL returns no extractable content
**When** the preview loads
**Then** the panel shows the empty-content message, not a blank panel

### AC-9: content_selector affects preview
**Given** the user has entered `content_selector = ".docs-content"`
**When** they click "Voorvertoning"
**Then** the preview request includes `content_selector: ".docs-content"`
**And** the preview result reflects the selector-filtered content

### AC-10: i18n
**Given** the portal language is EN
**Then** all preview-related strings render in English
**Given** the portal language is NL
**Then** all preview-related strings render in Dutch

---

## Implementation Notes

### Files to Change

**knowledge-ingest (3 files):**
- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` — REQ-1, REQ-2, REQ-3
- `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py` — REQ-4
- `klai-knowledge-ingest/knowledge_ingest/routes/knowledge.py` — REQ-5
- `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py` — REQ-6, REQ-7, REQ-8

**Portal backend (2 files):**
- `klai-portal/backend/app/services/knowledge_ingest_client.py` — REQ-9
- `klai-portal/backend/app/api/app_knowledge_bases.py` — REQ-10

**Frontend (3 files):**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` — REQ-11 through REQ-16
- `klai-portal/frontend/messages/en.json` — REQ-17
- `klai-portal/frontend/messages/nl.json` — REQ-17

### crawl.py Preview Endpoint Pattern

```python
class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None

class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int

@router.post("/crawl/preview", response_model=CrawlPreviewResponse)
async def preview_crawl(
    body: CrawlPreviewRequest,
    _: None = Depends(require_ingest_secret),
) -> CrawlPreviewResponse:
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
            ),
            css_selector=body.content_selector or None,
        )
        async with AsyncWebCrawler() as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=body.url, config=config),
                timeout=15.0,
            )
        fit_md = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown=fit_md,
            word_count=len(fit_md.split()),
        )
    except Exception as e:
        logger.warning("Preview crawl failed", url=body.url, error=str(e))
        return CrawlPreviewResponse(url=body.url, fit_markdown="", word_count=0)
```

### Frontend Preview State Pattern

```tsx
const [previewResult, setPreviewResult] = useState<{
  fit_markdown: string;
  word_count: number;
} | null>(null);

const previewMutation = useMutation({
  mutationFn: async ({ url, content_selector }: { url: string; content_selector?: string }) => {
    const res = await fetch(
      `${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ url, content_selector: content_selector || null }),
      }
    );
    if (!res.ok) return { fit_markdown: "", word_count: 0 };
    return res.json();
  },
  onSuccess: (data) => setPreviewResult(data),
  onError: () => setPreviewResult({ fit_markdown: "", word_count: 0 }),
});
```

Preview panel layout (below content_selector field):

```tsx
{previewResult !== null && (
  <div className="mt-3 rounded-lg border border-[var(--color-border)] p-3 space-y-2">
    <div className="flex items-center justify-between">
      <span className="text-sm font-medium text-[var(--color-purple-deep)]">
        {m.admin_connectors_webcrawler_preview_title()}
      </span>
      <span className="text-xs text-[var(--color-muted-foreground)]">
        {m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}
      </span>
    </div>
    {previewResult.fit_markdown ? (
      <pre className="text-xs text-[var(--color-muted-foreground)] overflow-y-auto max-h-48 whitespace-pre-wrap">
        {previewResult.fit_markdown}
      </pre>
    ) : (
      <p className="text-sm text-[var(--color-muted-foreground)]">
        {m.admin_connectors_webcrawler_preview_empty()}
      </p>
    )}
  </div>
)}
```

### knowledge_ingest_client.py Preview Method

```python
async def preview_crawl(self, url: str, content_selector: str | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{self.base_url}/ingest/v1/crawl/preview",
                headers={"X-Ingest-Secret": self.secret},
                json={"url": url, "content_selector": content_selector},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("preview_crawl failed", url=url, error=str(e))
        return {"fit_markdown": "", "word_count": 0, "url": url}
```
