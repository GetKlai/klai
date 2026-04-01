"""
Crawl route:
  POST /ingest/v1/crawl         — fetch a URL, convert HTML to markdown, and ingest
  POST /ingest/v1/crawl/preview — fetch a URL with PruningContentFilter and return fit_markdown

Pipeline selection (SPEC-CRAWL-001 / R-1):
- No selector (no user selector AND no stored domain selector):
    full pipeline — _JS_REMOVE_CHROME, excluded_tags, PruningContentFilter
- Selector present (user-provided or stored domain selector):
    trusted pipeline — no JS chrome removal, no excluded_tags, css_selector=selector
"""
import asyncio
import hashlib
import logging
import re
import time
from urllib.parse import urlparse

import html2text
import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from knowledge_ingest import pg_store
from knowledge_ingest.db import get_pool
from knowledge_ingest.domain_selectors import extract_domain, get_domain_selector, upsert_domain_selector
from knowledge_ingest.models import CrawlRequest, CrawlResponse, IngestRequest
from knowledge_ingest.routes.ingest import ingest_document
from knowledge_ingest.selector_ai import detect_selector_via_llm, extract_dom_summary
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
    org_id: str = ""  # optional for backwards compatibility; required for domain selector lookup


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []


# JS injected BEFORE wait_for: strip nav chrome so the word-count condition fires
# only when article content is present, not on pre-hydration nav words.
#
# IMPORTANT: use only semantic tag/role selectors here.
# Class/id substring selectors (e.g. [class*="sidebar"]) are dangerous — they
# match layout wrappers like class="has-sidebar" or class="sidebar-open" and
# delete the article element along with the wrapper. Verified with Playwright on
# help.voys.nl: [class*="sidebar"] removed the super-content-wrapper that wraps
# <main>, wiping all article content and producing raw_words=0.
_JS_REMOVE_CHROME = """
[
  'nav', 'header', 'footer', 'aside',
  '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
  '[role="search"]',
].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));
"""

# JS injected AFTER wait_for fires: open collapsed toggles (Notion/details).
# Nav removal is NOT repeated here — _JS_REMOVE_CHROME runs pre-hydration and
# excluded_tags handles any semantic nav/header/footer/aside in markdown generation.
# Running nav-removal JS post-hydration risks matching layout wrappers that React
# added during hydration (see note on _JS_REMOVE_CHROME above).
_JS_EXPAND_TOGGLES = """
document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
document.querySelectorAll('.notion-toggle__summary, [data-block-type="toggle"] > *:first-child').forEach(s => s.click());
await new Promise(r => setTimeout(r, 300));
"""


# Minimum word count for a crawl result to be considered usable content.
_MIN_WORD_COUNT = 100


def _build_crawl_config(selector: str | None):  # type: ignore[return]
    """Build a CrawlerRunConfig for the appropriate pipeline.

    - selector is None  → full pipeline (JS chrome removal, excluded_tags)
    - selector provided → trusted pipeline (no JS removal, no excluded_tags, css_selector set)

    SPEC-CRAWL-001 / R-1
    """
    from crawl4ai import CrawlerRunConfig, CacheMode  # noqa: PLC0415
    from crawl4ai.content_filter_strategy import PruningContentFilter  # noqa: PLC0415
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # noqa: PLC0415

    if selector:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            excluded_tags=[],
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
            ),
            css_selector=selector,
            js_code_before_wait=None,
            wait_for="js:() => document.body.innerText.trim().split(/\\s+/).length > 50",
            js_code=_JS_EXPAND_TOGGLES,
            remove_consent_popups=True,
            remove_overlay_elements=True,
            page_timeout=30000,
        )
    else:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            word_count_threshold=10,
            excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
            ),
            css_selector=None,
            js_code_before_wait=_JS_REMOVE_CHROME,
            wait_for="js:() => document.body.innerText.trim().split(/\\s+/).length > 50",
            js_code=_JS_EXPAND_TOGGLES,
            remove_consent_popups=True,
            remove_overlay_elements=True,
            page_timeout=30000,
        )


async def _run_crawl(url: str, selector: str | None) -> tuple[str, int]:
    """Run crawl4ai and return (fit_markdown, word_count)."""
    from crawl4ai import AsyncWebCrawler  # noqa: PLC0415

    config = _build_crawl_config(selector)
    async with AsyncWebCrawler() as crawler:
        result = await asyncio.wait_for(
            crawler.arun(url=url, config=config),
            timeout=30.0,
        )
    raw_md = result.markdown.raw_markdown or ""
    fit_md_raw = result.markdown.fit_markdown or ""
    preview_logger.info(
        "Crawl4ai result",
        url=url,
        selector=selector,
        raw_words=len(raw_md.split()),
        fit_words=len(fit_md_raw.split()),
        raw_preview=raw_md[:200],
    )
    fit_md = fit_md_raw or raw_md
    return fit_md, len(fit_md.split())


@router.post("/ingest/v1/crawl/preview", response_model=CrawlPreviewResponse)
async def preview_crawl(body: CrawlPreviewRequest) -> CrawlPreviewResponse:
    """Fetch a URL with PruningContentFilter and return the filtered markdown preview."""
    preview_logger.info("Preview crawl requested", url=body.url)
    try:
        # Resolve effective selector: user-provided wins, then stored domain selector
        # SPEC-CRAWL-001 / R-2, R-6
        effective_selector = body.content_selector
        selector_source = "user" if effective_selector else None

        if not effective_selector and body.org_id:
            stored = await get_domain_selector(extract_domain(body.url), body.org_id)
            if stored:
                effective_selector, selector_source = stored

        # Initial crawl
        fit_md, word_count = await _run_crawl(body.url, effective_selector)
        warnings: list[str] = _detect_nav_contamination(fit_md)

        # AI-assisted selector detection when result is too thin and no selector was used
        # SPEC-CRAWL-001 / R-4
        if word_count < _MIN_WORD_COUNT and not effective_selector and body.org_id:
            dom_summary = await extract_dom_summary(body.url)
            if dom_summary:
                ai_selector = await detect_selector_via_llm(dom_summary)
                if ai_selector:
                    try:
                        recrawl_md, recrawl_wc = await _run_crawl(body.url, ai_selector)
                        if recrawl_wc >= _MIN_WORD_COUNT:
                            await upsert_domain_selector(
                                extract_domain(body.url), body.org_id, ai_selector, "ai"
                            )
                            fit_md = recrawl_md
                            word_count = recrawl_wc
                            warnings = _detect_nav_contamination(fit_md)
                            effective_selector = ai_selector
                            selector_source = "ai"
                        else:
                            # Re-crawl also thin — return original, do not store
                            if "low_word_count" not in warnings:
                                warnings.append("low_word_count")
                    except Exception as exc:
                        preview_logger.warning(
                            "AI re-crawl failed",
                            url=body.url,
                            ai_selector=ai_selector,
                            error=str(exc),
                        )
                        if "low_word_count" not in warnings:
                            warnings.append("low_word_count")

        # Persist selector after a successful crawl (>= 100 words), if we have one
        # SPEC-CRAWL-001 / R-3
        if word_count >= _MIN_WORD_COUNT and effective_selector and body.org_id and selector_source:
            await upsert_domain_selector(
                extract_domain(body.url), body.org_id, effective_selector, selector_source
            )

        return CrawlPreviewResponse(
            url=body.url,
            fit_markdown=fit_md,
            word_count=word_count,
            warnings=warnings,
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

    # WARNING (pipeline config change): modifying the html2text settings below
    # (ignore_links, body_width, etc.) changes content_hash for every page even
    # when the actual page content has not changed. After such a change, force a
    # full re-ingest by clearing content_hash:
    #   UPDATE knowledge.crawled_pages
    #      SET content_hash = ''
    #    WHERE org_id = '<org>' AND kb_slug = '<slug>';

    # Fetch URL
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, verify=True) as client:
        try:
            resp = await client.get(request.url, headers={"User-Agent": "KlaiBot/1.0"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}")

    # Dual-hash dedup (see migration 012):
    #   1. raw_html_hash unchanged → skip everything (fast path, skips html2text too)
    #   2. raw_html_hash changed, content_hash unchanged → JS/tracking update, skip ingest
    #   3. both changed → real content change → full ingest
    raw_html_hash = hashlib.sha256(resp.text.encode()).hexdigest()
    stored = await pg_store.get_crawled_page_stored(
        request.org_id, request.kb_slug, request.url
    )

    def _derive_path() -> str:
        if request.path:
            return request.path
        parsed = urlparse(request.url)
        slug = parsed.path.strip("/").replace("/", "-") or parsed.netloc
        return f"{slug}.md"

    if stored is not None:
        stored_raw, _stored_content = stored
        if stored_raw is not None and stored_raw == raw_html_hash:
            logger.info("Crawl skipped (raw HTML unchanged): %s", request.url)
            return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

    # Convert HTML to markdown (only reached when raw HTML has changed or is new)
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    markdown = converter.handle(resp.text)
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()

    if stored is not None:
        _, stored_content = stored
        if stored_content is not None and stored_content == content_hash:
            # HTML changed (JS / tracking pixel) but article content is identical
            # → update raw_html_hash so future crawls hit the fast path, skip ingest
            await pg_store.upsert_crawled_page(
                org_id=request.org_id,
                kb_slug=request.kb_slug,
                url=request.url,
                raw_html_hash=raw_html_hash,
                content_hash=content_hash,
                raw_markdown=markdown,
                crawled_at=int(time.time()),
            )
            logger.info("Crawl skipped (HTML noise, content unchanged): %s", request.url)
            return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

    await pg_store.upsert_crawled_page(
        org_id=request.org_id,
        kb_slug=request.kb_slug,
        url=request.url,
        raw_html_hash=raw_html_hash,
        content_hash=content_hash,
        raw_markdown=markdown,
        crawled_at=int(time.time()),
    )

    path = _derive_path()

    # Ingest using existing pipeline (expects IngestRequest, returns dict)
    # SPEC-CRAWL-001 / R-5: include source_url in extra
    # SPEC-CRAWLER-003 R11: populate link graph fields when source_url present
    extra: dict = {"source_url": request.url}
    try:
        from knowledge_ingest import link_graph  # noqa: PLC0415
        pool = await get_pool()
        outbound, anchors, incoming = await asyncio.gather(
            link_graph.get_outbound_urls(request.url, request.org_id, request.kb_slug, pool),
            link_graph.get_anchor_texts(request.url, request.org_id, request.kb_slug, pool),
            link_graph.get_incoming_count(request.url, request.org_id, request.kb_slug, pool),
        )
        extra["links_to"] = outbound[:20]
        extra["anchor_texts"] = anchors
        extra["incoming_link_count"] = incoming
    except Exception as exc:
        logger.warning("link_graph_query_failed url=%s error=%s", request.url, exc)
    ingest_req = IngestRequest(
        org_id=request.org_id,
        kb_slug=request.kb_slug,
        path=path,
        content=markdown,
        extra=extra,
    )
    result = await ingest_document(ingest_req)
    n_chunks = result.get("chunks", 0)

    logger.info("Crawled and ingested %s -> %s (%d chunks)", request.url, path, n_chunks)
    return CrawlResponse(url=request.url, path=path, chunks_ingested=n_chunks)
