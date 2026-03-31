"""
Crawl route:
  POST /ingest/v1/crawl — fetch a URL, convert HTML to markdown, and ingest
  POST /ingest/v1/crawl/preview — fetch a URL with PruningContentFilter and return fit_markdown
"""
import asyncio
import logging
import re
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


_LINK_RE = re.compile(r"\[([^\]]*)\]\([^\)]+\)")


def _detect_nav_contamination(text: str) -> list[str]:
    """Detect navigation/menu contamination in fit_markdown.

    Two signals that BOTH must fire (conservative — false positives are worse than misses):
    - link_density:  >35% of all non-empty lines are 'link-only' (link(s) with ≤2 prose words)
    - top_heavy:     >45% of the first 25 lines are link-only

    Returns ["navigation_detected"] when contamination is likely, [] otherwise.
    """
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 15 or len(text.split()) < 50:
        return []

    def _is_nav_line(line: str) -> bool:
        stripped = line.strip("*-># \t|")
        links = _LINK_RE.findall(stripped)
        if not links:
            return False
        remaining = _LINK_RE.sub("", stripped).strip(" |,-•·")
        return len(remaining.split()) <= 2

    nav_count = sum(1 for ln in lines if _is_nav_line(ln))
    nav_ratio = nav_count / len(lines)

    first_n = lines[: min(25, len(lines))]
    first_nav = sum(1 for ln in first_n if _is_nav_line(ln))
    first_nav_ratio = first_nav / len(first_n)

    if nav_ratio > 0.35 and first_nav_ratio > 0.45:
        return ["navigation_detected"]
    return []


class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []


# JS injected BEFORE the wait_for condition is evaluated.
# Removes navigation/chrome so the wait_for word-count check fires only when
# the main article content (not nav) is present — critical for React/Next.js sites
# where pre-hydration nav already contains 50+ words.
_JS_REMOVE_CHROME = """
[
  'nav', 'header', 'footer', 'aside',
  '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
  '[class*="navbar" i]', '[class*="nav-bar" i]', '[class*="nav-menu" i]',
  '[class*="site-header" i]', '[class*="site-footer" i]', '[class*="site-nav" i]',
  '[class*="page-header" i]', '[class*="page-footer" i]',
  '[class*="top-bar" i]', '[class*="topbar" i]',
  '[class*="breadcrumb" i]',
  '[class*="sidebar" i]', '[class*="side-bar" i]', '[class*="sidenav" i]', '[class*="side-nav" i]',
  '[role="search"]',
  '[id*="navbar" i]', '[id*="nav-bar" i]', '[id*="site-nav" i]',
  '[id*="header" i]', '[id*="footer" i]', '[id*="sidebar" i]',
  // Super.so (Notion-based help centers)
  '.super-navbar', '.super-sidebar', '.super-footer',
].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));
"""

# JS injected AFTER wait_for fires (React has now rendered the full DOM).
# Re-runs nav removal (React rebuilds nav during hydration) and opens collapsed toggles.
# Cookie/consent removal is handled by remove_consent_popups=True (built-in, runs after js_code).
_JS_CLEAN_POST_HYDRATION = _JS_REMOVE_CHROME + """
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
            js_code_before_wait=_JS_REMOVE_CHROME,
            wait_for="js:() => document.body.innerText.trim().split(/\\s+/).length > 50",
            js_code=_JS_CLEAN_POST_HYDRATION,
            remove_consent_popups=True,
            remove_overlay_elements=True,
            page_timeout=30000,
        )
        async with AsyncWebCrawler() as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=body.url, config=config),
                timeout=30.0,
            )
        raw_md = result.markdown.raw_markdown or ""
        fit_md_raw = result.markdown.fit_markdown or ""
        preview_logger.info(
            "Crawl4ai result",
            url=body.url,
            raw_words=len(raw_md.split()),
            fit_words=len(fit_md_raw.split()),
            raw_preview=raw_md[:200],
        )
        fit_md = fit_md_raw or raw_md
        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown=fit_md,
            word_count=len(fit_md.split()),
            warnings=_detect_nav_contamination(fit_md),
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
