"""
Web crawler adapter: bulk-crawls a website and ingests each page.
Uses the Crawl4AI REST API (shared Docker container) for all crawling.
"""
from __future__ import annotations

import asyncio
import hashlib
import time

import structlog

from knowledge_ingest import pg_store
from knowledge_ingest.crawl4ai_client import CrawlResult, crawl_site
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
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    rate_limit: float = 2.0,
    content_selector: str | None = None,
) -> None:
    """
    Crawl a website and ingest each page into the knowledge pipeline.
    Updates knowledge.crawl_jobs progress as pages are processed.

    Seeds the crawl with start_url + sitemap.xml, then recurses to max_depth
    using Crawl4AI's BFSDeepCrawlStrategy (same strategy as klai-connector).

    Note: exclude_patterns is accepted for API compatibility but not forwarded
    to Crawl4AI (URLPatternFilter supports include-only).  rate_limit is also
    accepted for compatibility; Crawl4AI manages its own request pacing.
    """
    await _update_job(job_id, status="running")

    pages_done = 0
    pages_failed = 0

    try:
        results = await crawl_site(
            start_url=start_url,
            selector=content_selector,
            max_depth=max_depth,
            max_pages=max_pages,
            include_patterns=include_patterns,
        )

        pool = await get_pool()
        await pool.execute(
            "UPDATE knowledge.crawl_jobs SET pages_total=$1, updated_at=$2 WHERE id=$3",
            len(results), int(time.time()), job_id,
        )

        # Batch-fetch all known content hashes in a single query
        urls = [r.url for r in results]
        known_hashes = await pg_store.get_crawled_page_hashes(org_id, kb_slug, urls)

        for result in results:
            url = result.url
            try:
                await _ingest_crawl_result(
                    result, url, org_id, kb_slug,
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
            from knowledge_ingest import (
                link_graph,
                qdrant_store,
            )

            url_to_count = await link_graph.compute_incoming_counts(org_id, kb_slug, pool)
            if url_to_count:
                await qdrant_store.update_link_counts(org_id, kb_slug, url_to_count)
                logger.info(
                    "link_counts_updated", job_id=job_id, count=len(url_to_count)
                )
        except Exception as exc:
            logger.warning("link_counts_update_failed", job_id=job_id, error=str(exc))

        await _update_job(job_id, status="completed")
        logger.info(
            "crawl_job_complete", job_id=job_id,
            pages_done=pages_done, pages_failed=pages_failed,
        )

    except Exception as exc:
        logger.exception("crawl_job_error", job_id=job_id, error=str(exc))
        await _update_job(job_id, status="failed", error=str(exc))


async def _ingest_crawl_result(
    result: CrawlResult,
    url: str,
    org_id: str,
    kb_slug: str,
    pool: object | None = None,
    stored: pg_store.PageHashes | None | object = _UNSET,
) -> None:
    """Process a crawl result: dedup, extract links, ingest.

    WARNING (pipeline config change): modifying crawl4ai settings in
    crawl4ai_client.build_crawl_config() changes content_hash for every page
    even when the actual page content has not changed.  After such a change,
    force a full re-ingest by clearing content_hash:
      UPDATE knowledge.crawled_pages
         SET content_hash = ''
       WHERE org_id = '<org>' AND kb_slug = '<slug>';
    """
    if not result.success:
        raise ValueError(f"Crawl failed: {result.error_message}")

    # Detect PDF: check Content-Type header first, fall back to URL extension
    content_type_header = ""
    if result.response_headers:
        content_type_header = result.response_headers.get("content-type", "")
    is_pdf = "application/pdf" in content_type_header or url.lower().endswith(".pdf")
    content_type = "pdf_document" if is_pdf else "kb_article"

    # Dual-hash dedup (see migration 012)
    if stored is _UNSET:
        stored = await pg_store.get_crawled_page_stored(org_id, kb_slug, url)

    raw_html = result.html or ""
    raw_html_hash = hashlib.sha256(raw_html.encode()).hexdigest()

    if stored is not None:
        stored_raw, _stored_content = stored  # type: ignore[misc]
        if stored_raw is not None and stored_raw == raw_html_hash:
            logger.info("crawl_skipped_unchanged", url=url, org_id=org_id, kb_slug=kb_slug)
            return

    text = result.fit_markdown or result.raw_markdown or ""
    front_matter = (result.metadata or {}).get("description", "")

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if stored is not None:
        _, stored_content = stored  # type: ignore[misc]
        if stored_content is not None and stored_content == content_hash:
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
        internal_links = result.links.get("internal", [])
        if internal_links:
            await pg_store.upsert_page_links(
                org_id=org_id,
                kb_slug=kb_slug,
                from_url=url,
                links=internal_links,
            )

    extra: dict = {"source_url": url, "crawled_at": int(time.time())}
    if is_pdf and front_matter:
        extra["front_matter"] = front_matter

    # SPEC-CRAWLER-003 R11: populate link graph fields after page_links upsert
    try:
        from knowledge_ingest import link_graph

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
