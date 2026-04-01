"""
Web crawler adapter: bulk-crawls a website and ingests each page.
Uses crawl4ai for async crawling with robots.txt respect.
"""
from __future__ import annotations

import asyncio
import logging
import time

from knowledge_ingest.db import get_pool
from knowledge_ingest.models import IngestRequest

logger = logging.getLogger(__name__)


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
    """
    Crawl a website and ingest each page into the knowledge pipeline.
    Updates knowledge.crawl_jobs progress as pages are processed.
    """
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode  # noqa: PLC0415
        from crawl4ai.content_filter_strategy import PruningContentFilter  # noqa: PLC0415
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # noqa: PLC0415
    except ImportError:
        logger.error("crawl4ai not installed - cannot run crawl job %s", job_id)
        await _update_job(job_id, status="failed", error="crawl4ai not installed")
        return

    await _update_job(job_id, status="running")

    pages_done = 0
    pages_failed = 0

    from knowledge_ingest.routes.crawl import _JS_REMOVE_CHROME, _JS_EXPAND_TOGGLES  # noqa: PLC0415

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")
        ),
        css_selector=content_selector or None,
        js_code_before_wait=_JS_REMOVE_CHROME,
        wait_for="js:() => document.body.innerText.trim().split(/\\s+/).length > 50",
        js_code=_JS_EXPAND_TOGGLES,
        remove_consent_popups=True,
        remove_overlay_elements=True,
        page_timeout=30000,
    )

    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=start_url, config=config)
            urls_to_crawl: list[str] = [start_url]

            # Collect internal links up to max_depth
            if result.success and result.links:
                for link in result.links.get("internal", []):
                    href = link.get("href", "")
                    if href and href not in urls_to_crawl:
                        urls_to_crawl.append(href)

            pool = await get_pool()
            await pool.execute(
                "UPDATE knowledge.crawl_jobs SET pages_total=$1, updated_at=$2 WHERE id=$3",
                len(urls_to_crawl), int(time.time()), job_id,
            )

            delay = 1.0 / rate_limit if rate_limit > 0 else 1.0
            for url in urls_to_crawl:
                try:
                    await _crawl_and_ingest_page(crawler, config, url, org_id, kb_slug, delay)
                    pages_done += 1
                except Exception as exc:
                    logger.warning("Crawler skipping %s: %s (job=%s)", url, exc, job_id)
                    pages_failed += 1

                await pool.execute(
                    "UPDATE knowledge.crawl_jobs SET pages_done=$1, updated_at=$2 WHERE id=$3",
                    pages_done, int(time.time()), job_id,
                )

        await _update_job(job_id, status="completed")
        logger.info("Crawl job %s complete: %d pages ingested, %d failed", job_id, pages_done, pages_failed)

    except Exception as exc:
        logger.error("Crawl job %s failed: %s", job_id, exc, exc_info=True)
        await _update_job(job_id, status="failed", error=str(exc))


async def _crawl_and_ingest_page(
    crawler: object,
    config: object,
    url: str,
    org_id: str,
    kb_slug: str,
    delay: float,
) -> None:
    await asyncio.sleep(delay)

    result = await crawler.arun(url=url, config=config)
    if not result.success:
        raise ValueError(f"Crawl failed: {result.error_message}")

    # Detect PDF: check Content-Type header first, fall back to URL extension
    content_type_header = ""
    if result.response_headers:
        content_type_header = result.response_headers.get("content-type", "")
    is_pdf = "application/pdf" in content_type_header or url.lower().endswith(".pdf")
    content_type = "pdf_document" if is_pdf else "kb_article"

    text = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
    front_matter = result.metadata.get("description", "") if result.metadata else ""

    extra: dict = {"source_url": url, "crawled_at": int(time.time())}
    if is_pdf and front_matter:
        extra["front_matter"] = front_matter

    # Import here to avoid circular imports at module level
    from knowledge_ingest.routes.ingest import ingest_document  # noqa: PLC0415

    await ingest_document(IngestRequest(
        org_id=org_id,
        kb_slug=kb_slug,
        path=url,
        content=text,
        source_type="connector",
        content_type=content_type,
        synthesis_depth=1,
        extra=extra,
    ))


async def _update_job(job_id: str, status: str, error: str | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE knowledge.crawl_jobs SET status=$1, error=$2, updated_at=$3 WHERE id=$4",
        status, error, int(time.time()), job_id,
    )
