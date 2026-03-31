"""
Crawl route:
  POST /ingest/v1/crawl — fetch a URL, convert HTML to markdown, and ingest
  POST /ingest/v1/crawl/preview — fetch a URL with PruningContentFilter and return fit_markdown
"""
import asyncio
import logging
from urllib.parse import urlparse

import html2text
import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from knowledge_ingest.models import CrawlRequest, CrawlResponse, IngestRequest
from knowledge_ingest.routes.ingest import ingest_document
from knowledge_ingest.utils.url_validator import validate_url

logger = logging.getLogger(__name__)
preview_logger = structlog.get_logger()
router = APIRouter()


class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int


# JS injected before content extraction:
#  1. Dismiss cookie/consent popups (click decline, then nuke DOM nodes)
#  2. Open native <details> and JS-rendered toggles (Notion, etc.)
_JS_PREPARE_PAGE = """
// --- 1. Dismiss cookie banners ---
const declineBtns = [
  '[aria-label*="decline" i]', '[aria-label*="reject" i]', '[aria-label*="weiger" i]',
  'button[id*="decline" i]', 'button[id*="reject" i]', 'button[id*="weiger" i]',
  'button[class*="decline" i]', 'button[class*="reject" i]',
  '.ppms_cm_popup__btn--secondary',  // Piwik PRO
  '#onetrust-reject-all-handler',    // OneTrust
  '.cc-deny',                        // cookieconsent
  '[data-testid*="decline" i]',
];
for (const sel of declineBtns) {
  const btn = document.querySelector(sel);
  if (btn) { btn.click(); break; }
}
await new Promise(r => setTimeout(r, 300));

// Remove leftover overlay containers
[
  '.ppms_cm_popup_overlay', '.ppms_cm_popup',
  '#onetrust-banner-sdk', '#onetrust-consent-sdk',
  '.cc-window', '.cookie-banner', '.cookie-notice', '.cookie-consent',
  '[id*="cookie-banner" i]', '[id*="consent-banner" i]',
  '[class*="cookie-banner" i]', '[class*="consent-banner" i]',
  '[aria-label*="cookie" i]',
].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));

// --- 2. Open toggles ---
document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
document.querySelectorAll('.notion-toggle__summary, [data-block-type="toggle"] > *:first-child').forEach(s => s.click());
await new Promise(r => setTimeout(r, 600));
"""


@router.post("/ingest/v1/crawl/preview", response_model=CrawlPreviewResponse)
async def preview_crawl(body: CrawlPreviewRequest) -> CrawlPreviewResponse:
    """Fetch a URL with PruningContentFilter and return the filtered markdown preview."""
    preview_logger.info("Preview crawl requested", url=body.url)
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode  # noqa: PLC0415
        from crawl4ai.content_filter_strategy import PruningContentFilter  # noqa: PLC0415
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # noqa: PLC0415

        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
            ),
            css_selector=body.content_selector or None,
            js_code=_JS_PREPARE_PAGE,
        )
        async with AsyncWebCrawler() as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=body.url, config=config),
                timeout=30.0,
            )
        fit_md = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown=fit_md,
            word_count=len(fit_md.split()),
        )
    except Exception as exc:
        preview_logger.warning("Preview crawl failed", url=body.url, error=str(exc))
        return CrawlPreviewResponse(url=body.url, fit_markdown="", word_count=0)


@router.post("/ingest/v1/crawl", response_model=CrawlResponse)
async def crawl_url(request: CrawlRequest) -> CrawlResponse:
    """Fetch a URL, convert HTML to markdown, and ingest via the standard pipeline."""
    try:
        await validate_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Fetch URL
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, verify=True) as client:
        try:
            resp = await client.get(request.url, headers={"User-Agent": "KlaiBot/1.0"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}")

    # Convert HTML to markdown
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    markdown = converter.handle(resp.text)

    # Derive path from URL if not provided
    path = request.path
    if not path:
        parsed = urlparse(request.url)
        slug = parsed.path.strip("/").replace("/", "-") or parsed.netloc
        path = f"{slug}.md"

    # Ingest using existing pipeline (expects IngestRequest, returns dict)
    ingest_req = IngestRequest(
        org_id=request.org_id,
        kb_slug=request.kb_slug,
        path=path,
        content=markdown,
    )
    result = await ingest_document(ingest_req)
    n_chunks = result.get("chunks", 0)

    logger.info("Crawled and ingested %s -> %s (%d chunks)", request.url, path, n_chunks)
    return CrawlResponse(url=request.url, path=path, chunks_ingested=n_chunks)
