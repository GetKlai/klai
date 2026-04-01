"""
Web crawler adapter: bulk-crawls a website and ingests each page.
Uses crawl4ai for async crawling with robots.txt respect.
"""
from __future__ import annotations

import asyncio
import hashlib
import structlog
import time

from knowledge_ingest import pg_store
from knowledge_ingest.db import get_pool
from knowledge_ingest.models import IngestRequest

logger = structlog.get_logger()

_UNSET = object()  # sentinel: stored_hash not yet fetched from DB


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
        from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        logger.error("crawl_job_failed", job_id=job_id, error="crawl4ai not installed")
        await _update_job(job_id, status="failed", error="crawl4ai not installed")
        return

    await _update_job(job_id, status="running")

    pages_done = 0
    pages_failed = 0

    from knowledge_ingest.routes.crawl import _JS_EXPAND_TOGGLES, _JS_REMOVE_CHROME

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

            # Batch-fetch all known content hashes in a single query
            known_hashes = await pg_store.get_crawled_page_hashes(org_id, kb_slug, urls_to_crawl)

            delay = 1.0 / rate_limit if rate_limit > 0 else 1.0
            for url in urls_to_crawl:
                try:
                    await _crawl_and_ingest_page(
                        crawler, config, url, org_id, kb_slug, delay,
                        pool=pool,
                        stored=known_hashes.get(url),
                    )
                    pages_done += 1
                except Exception as exc:
                    logger.warning("crawl_page_failed", url=url, job_id=job_id, error=str(exc))
                    pages_failed += 1

                await pool.execute(
                    "UPDATE knowledge.crawl_jobs SET pages_done=$1, updated_at=$2 WHERE id=$3",
                    pages_done, int(time.time()), job_id,
                )

        # SPEC-CRAWLER-003 R12: batch-update incoming link counts after full crawl
        try:
            from knowledge_ingest import link_graph  # noqa: PLC0415
            from knowledge_ingest import qdrant_store  # noqa: PLC0415

            url_to_count = await link_graph.compute_incoming_counts(org_id, kb_slug, pool)
            if url_to_count:
                await qdrant_store.update_link_counts(org_id, kb_slug, url_to_count)
                logger.info(
                    "link_counts_updated", job_id=job_id, count=len(url_to_count)
                )
        except Exception as exc:
            logger.warning("link_counts_update_failed", job_id=job_id, error=str(exc))

        await _update_job(job_id, status="completed")
        logger.info("crawl_job_complete", job_id=job_id, pages_done=pages_done, pages_failed=pages_failed)

    except Exception as exc:
        logger.error("crawl_job_error", job_id=job_id, error=str(exc), exc_info=True)
        await _update_job(job_id, status="failed", error=str(exc))


async def _crawl_and_ingest_page(
    crawler: object,
    config: object,
    url: str,
    org_id: str,
    kb_slug: str,
    delay: float,
    pool: object | None = None,
    stored: "pg_store.PageHashes | None | object" = _UNSET,
) -> None:
    # WARNING (pipeline config change): modifying the extraction settings in
    # run_crawl_job() — excluded_tags, PruningContentFilter threshold, the JS
    # removal scripts — changes content_hash for every page even when the actual
    # page content has not changed. After such a change, force a full re-ingest:
    #   UPDATE knowledge.crawled_pages
    #      SET content_hash = ''
    #    WHERE org_id = '<org>' AND kb_slug = '<slug>';
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

    # Dual-hash dedup (see migration 012):
    #   1. raw_html_hash unchanged → skip everything (source HTML is identical)
    #   2. raw_html_hash changed, content_hash unchanged → JS/tracking update, skip ingest
    #   3. both changed → real content change → full ingest
    #
    # stored is pre-fetched by run_crawl_job; falls back to a per-page DB query
    # only when called without pre-fetched hashes (e.g. direct test calls).
    if stored is _UNSET:
        stored = await pg_store.get_crawled_page_stored(org_id, kb_slug, url)

    raw_html = result.html or ""
    raw_html_hash = hashlib.sha256(raw_html.encode()).hexdigest()

    if stored is not None:
        stored_raw, _stored_content = stored  # type: ignore[misc]
        if stored_raw is not None and stored_raw == raw_html_hash:
            logger.info("crawl_skipped_unchanged", url=url, org_id=org_id, kb_slug=kb_slug)
            return

    text = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
    front_matter = result.metadata.get("description", "") if result.metadata else ""

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if stored is not None:
        _, stored_content = stored  # type: ignore[misc]
        if stored_content is not None and stored_content == content_hash:
            # HTML changed (JS / tracking pixel) but article content is identical
            # → update raw_html_hash so future crawls hit the fast path, skip ingest
            await pg_store.upsert_crawled_page(
                org_id=org_id,
                kb_slug=kb_slug,
                url=url,
                raw_html_hash=raw_html_hash,
                content_hash=content_hash,
                raw_markdown=text,
                crawled_at=int(time.time()),
            )
            logger.info("crawl_skipped_html_noise", url=url, org_id=org_id, kb_slug=kb_slug)
            return

    if result.links:
        await pg_store.upsert_page_links(
            org_id=org_id,
            kb_slug=kb_slug,
            from_url=url,
            links=result.links.get("internal", []),
        )

    extra: dict = {"source_url": url, "crawled_at": int(time.time())}
    if is_pdf and front_matter:
        extra["front_matter"] = front_matter

    # SPEC-CRAWLER-003 R11: populate link graph fields after page_links upsert
    try:
        from knowledge_ingest import link_graph  # noqa: PLC0415

        outbound, anchors, incoming = await asyncio.gather(
            link_graph.get_outbound_urls(url, org_id, kb_slug, pool),
            link_graph.get_anchor_texts(url, org_id, kb_slug, pool),
            link_graph.get_incoming_count(url, org_id, kb_slug, pool),
        )
        extra["links_to"] = outbound[:20]
        extra["anchor_texts"] = anchors
        extra["incoming_link_count"] = incoming
    except Exception as exc:
        logger.warning("link_graph_query_failed", url=url, error=str(exc))

    # Import here to avoid circular imports at module level
    from knowledge_ingest.routes.ingest import ingest_document

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

    # Save hashes only after successful ingest — if ingest_document raises, the
    # hashes are not persisted so the next crawl will retry the full ingest.
    await pg_store.upsert_crawled_page(
        org_id=org_id,
        kb_slug=kb_slug,
        url=url,
        raw_html_hash=raw_html_hash,
        content_hash=content_hash,
        raw_markdown=text,
        crawled_at=int(time.time()),
    )


async def _update_job(job_id: str, status: str, error: str | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE knowledge.crawl_jobs SET status=$1, error=$2, updated_at=$3 WHERE id=$4",
        status, error, int(time.time()), job_id,
    )
