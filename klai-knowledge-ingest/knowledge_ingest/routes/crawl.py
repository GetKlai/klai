"""
Crawl route:
  POST /ingest/v1/crawl         — fetch a URL via Crawl4AI REST API, convert to markdown, and ingest
  POST /ingest/v1/crawl/preview — fetch a URL with PruningContentFilter and return fit_markdown

All crawling goes through the shared Crawl4AI Docker container via crawl4ai_client.
Pipeline selection (SPEC-CRAWL-001 / R-1) is handled by crawl4ai_client.build_crawl_config().
"""
import asyncio
import hashlib
import re
import time
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from knowledge_ingest import pg_store
from knowledge_ingest.crawl4ai_client import crawl_dom_summary, crawl_page
from knowledge_ingest.db import get_pool
from knowledge_ingest.domain_selectors import (
    extract_domain,
    get_domain_selector,
    upsert_domain_selector,
)
from knowledge_ingest.models import CrawlRequest, CrawlResponse, IngestRequest
from knowledge_ingest.routes.ingest import ingest_document
from knowledge_ingest.selector_ai import detect_selector_via_llm
from knowledge_ingest.utils.url_validator import validate_url

logger = structlog.get_logger()
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
    try_ai: bool = False  # explicit opt-in for AI selector detection
    cookies: list[dict] | None = None  # browser cookies for authenticated crawling


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []
    content_selector: str | None = None
    selector_source: str | None = None  # "user" | "ai" | None


# Minimum word count for a crawl result to be considered usable content.
_MIN_WORD_COUNT = 100


async def _run_crawl(
    url: str,
    selector: str | None,
    cookies: list[dict] | None = None,
) -> tuple[str, int, str]:
    """Crawl a single page via the Crawl4AI REST API.

    Returns (fit_markdown, word_count, raw_html).
    """
    result = await crawl_page(url, selector, cookies=cookies)
    fit_md = result.fit_markdown or result.raw_markdown
    return fit_md, result.word_count, result.html


@router.post("/ingest/v1/crawl/preview", response_model=CrawlPreviewResponse)
async def preview_crawl(body: CrawlPreviewRequest) -> CrawlPreviewResponse:
    """Fetch a URL with PruningContentFilter and return the filtered markdown preview."""
    logger.info("crawl_preview_started", url=body.url)
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
        fit_md, word_count, _ = await _run_crawl(body.url, effective_selector, cookies=body.cookies)
        warnings: list[str] = _detect_nav_contamination(fit_md)

        # AI-assisted selector detection — only when explicitly requested via try_ai flag
        # SPEC-CRAWL-001 / R-4
        if body.try_ai and word_count < _MIN_WORD_COUNT and not effective_selector and body.org_id:
            dom_summary = await crawl_dom_summary(body.url)
            if dom_summary:
                ai_selector = await detect_selector_via_llm(dom_summary)
                if ai_selector:
                    try:
                        recrawl_md, recrawl_wc, _ = await _run_crawl(
                            body.url, ai_selector, cookies=body.cookies,
                        )
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
                        logger.warning(
                            "crawl_ai_recrawl_failed",
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
            content_selector=effective_selector,
            selector_source=selector_source,
        )
    except Exception as exc:
        logger.warning("crawl_preview_failed", url=body.url, error=str(exc))
        return CrawlPreviewResponse(url=body.url, fit_markdown="", word_count=0)


@router.post("/ingest/v1/crawl", response_model=CrawlResponse)
async def crawl_url(request: CrawlRequest) -> CrawlResponse:
    """Fetch a URL with crawl4ai and ingest via the standard pipeline.

    Uses the same crawl4ai pipeline as the bulk crawler and preview endpoint,
    so JS-rendered pages (SPAs) are handled correctly and content_hash is
    consistent across all crawl paths.
    """
    try:
        await validate_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _derive_path() -> str:
        if request.path:
            return request.path
        parsed = urlparse(request.url)
        slug = parsed.path.strip("/").replace("/", "-") or parsed.netloc
        return f"{slug}.md"

    # Resolve stored domain selector so the right pipeline is used
    # SPEC-CRAWL-001 / R-2
    effective_selector: str | None = None
    if request.org_id:
        stored_sel = await get_domain_selector(extract_domain(request.url), request.org_id)
        if stored_sel:
            effective_selector, _ = stored_sel

    # WARNING (pipeline config change): modifying crawl4ai settings in
    # crawl4ai_client.build_crawl_config() changes content_hash for every page
    # even when the actual page content has not changed.  After such a change,
    # force a full re-ingest by clearing content_hash:
    #   UPDATE knowledge.crawled_pages
    #      SET content_hash = ''
    #    WHERE org_id = '<org>' AND kb_slug = '<slug>';
    fit_md, _word_count, raw_html = await _run_crawl(request.url, effective_selector)

    # Dual-hash dedup (see migration 012):
    #   1. raw_html_hash unchanged → skip everything (fast path)
    #   2. raw_html_hash changed, content_hash unchanged → JS/tracking update, skip ingest
    #   3. both changed → real content change → full ingest
    raw_html_hash = hashlib.sha256(raw_html.encode()).hexdigest()
    stored = await pg_store.get_crawled_page_stored(
        request.org_id, request.kb_slug, request.url
    )

    if stored is not None:
        stored_raw, _stored_content = stored
        if stored_raw is not None and stored_raw == raw_html_hash:
            logger.info("crawl_skipped_unchanged", url=request.url)
            return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

    content_hash = hashlib.sha256(fit_md.encode()).hexdigest()

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
                raw_markdown=fit_md,
                crawled_at=int(time.time()),
            )
            logger.info("crawl_skipped_html_noise", url=request.url)
            return CrawlResponse(url=request.url, path=_derive_path(), chunks_ingested=0)

    await pg_store.upsert_crawled_page(
        org_id=request.org_id,
        kb_slug=request.kb_slug,
        url=request.url,
        raw_html_hash=raw_html_hash,
        content_hash=content_hash,
        raw_markdown=fit_md,
        crawled_at=int(time.time()),
    )

    path = _derive_path()

    # Ingest using existing pipeline (expects IngestRequest, returns dict)
    # SPEC-CRAWL-001 / R-5: include source_url in extra
    # SPEC-CRAWLER-003 R11: populate link graph fields when source_url present
    extra: dict = {"source_url": request.url}
    try:
        from knowledge_ingest import link_graph
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
        logger.warning("link_graph_query_failed", url=request.url, error=str(exc))
    ingest_req = IngestRequest(
        org_id=request.org_id,
        kb_slug=request.kb_slug,
        path=path,
        content=fit_md,
        extra=extra,
    )
    result = await ingest_document(ingest_req)
    n_chunks = result.get("chunks", 0)

    logger.info("crawl_ingest_complete", url=request.url, path=path, chunks=n_chunks)
    return CrawlResponse(url=request.url, path=path, chunks_ingested=n_chunks)
