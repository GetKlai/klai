"""
Web crawler adapter: bulk-crawls a website and ingests each page.
Uses the Crawl4AI REST API (shared Docker container) for all crawling.
"""
from __future__ import annotations

import asyncio
import hashlib
import time

import httpx
import structlog

from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import CrawlResult, crawl_site
from knowledge_ingest.db import get_pool
from knowledge_ingest.models import IngestRequest
from knowledge_ingest.s3_storage import ImageStore
from knowledge_ingest.sync_images import download_and_upload_crawl_images

logger = structlog.get_logger()


# @MX:ANCHOR: AuthWallDetected -- propagates login-indicator triggers from _ingest_crawl_result
#   back up to run_crawl_job, which converts them into a single structured
#   crawl_jobs.error entry and halts the remaining BFS pages.
# @MX:REASON: A silent auth wall would otherwise ingest login pages as "content"
#   and pollute Qdrant. Hard failing with a typed exception keeps the error
#   surface at exactly one row per sync regardless of page count.
# @MX:SPEC: SPEC-CRAWLER-004 REQ-02.3
class AuthWallDetected(Exception):
    """Raised when a page matches the configured ``login_indicator_selector``.

    Attributes:
        selector: The CSS selector that matched. Included in ``crawl_jobs.error``
            so operators can tell which indicator fired without reading logs.
    """

    def __init__(self, selector: str) -> None:
        super().__init__(f"auth_wall_detected: {selector}")
        self.selector = selector


def _build_image_store() -> ImageStore | None:
    """Construct an ImageStore from settings, or None if disabled.

    SPEC-CRAWLER-004 Fase A — empty ``garage_s3_endpoint`` means the image
    pipeline is turned off (e.g. in dev where Garage is not provisioned).
    """
    if not settings.garage_s3_endpoint:
        return None
    return ImageStore(
        endpoint=settings.garage_s3_endpoint,
        access_key=settings.garage_access_key,
        secret_key=settings.garage_secret_key,
        bucket=settings.garage_bucket,
        region=settings.garage_region,
    )

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
    login_indicator_selector: str | None = None,
    cookies: list[dict] | None = None,
    canary_url: str | None = None,
    canary_fingerprint: str | None = None,
) -> None:
    """
    Crawl a website and ingest each page into the knowledge pipeline.
    Updates knowledge.crawl_jobs progress as pages are processed.

    Seeds the crawl with start_url + sitemap.xml, then recurses to max_depth
    using Crawl4AI's BFSDeepCrawlStrategy (same strategy as klai-connector).

    Note: exclude_patterns is accepted for API compatibility but not forwarded
    to Crawl4AI (URLPatternFilter supports include-only).  rate_limit is also
    accepted for compatibility; Crawl4AI manages its own request pacing.

    ``login_indicator_selector`` (SPEC-CRAWLER-004 Fase B / REQ-02.3) is
    injected into crawl4ai's wait_for and also re-checked on every returned
    page. If any page is flagged as auth-walled the job is marked failed
    with ``error='auth_wall_detected: {selector}'`` and no further pages
    are ingested.
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
            login_indicator_selector=login_indicator_selector,
            cookies=cookies,
        )
        # canary_url / canary_fingerprint are accepted for forwards-compat with
        # the /ingest/v1/crawl/sync request body; they are plumbed here but the
        # bulk crawl does not yet evaluate them (SPEC-CRAWL-004 canary check
        # currently lives in the preview endpoint). Declaring + no-opping them
        # keeps the public signature stable for Fase D delegation.
        _ = (canary_url, canary_fingerprint)

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
            # SPEC-CRAWLER-004 Fase B: detect login-indicator trigger.
            # crawl4ai returns success=False when the injected wait_for times
            # out on the login selector; surfacing that as AuthWallDetected
            # gives us a single structured failure per sync.
            if login_indicator_selector and not result.success:
                raise AuthWallDetected(login_indicator_selector)
            try:
                await _ingest_crawl_result(
                    result, url, org_id, kb_slug,
                    pool=pool,
                    stored=known_hashes.get(url),
                    login_indicator_selector=login_indicator_selector,
                )
                pages_done += 1
            except AuthWallDetected:
                # Halt the whole BFS — downstream handler in the except block
                # writes the job row; do not keep ingesting follow-up pages.
                raise
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

    except AuthWallDetected as exc:
        # SPEC-CRAWLER-004 REQ-02.3: one structured error per sync, no artifacts.
        logger.error(
            "crawl_job_auth_wall",
            job_id=job_id, selector=exc.selector, pages_ingested=pages_done,
        )
        await _update_job(job_id, status="failed", error=str(exc))
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
    login_indicator_selector: str | None = None,
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
        # With a login indicator set, crawl4ai's wait_for fails on auth-walled
        # pages and returns success=False. run_crawl_job catches this first
        # (before calling us), but guard here too so a direct caller still
        # surfaces the typed exception instead of a generic ValueError.
        if login_indicator_selector:
            raise AuthWallDetected(login_indicator_selector)
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

    # SPEC-CRAWLER-004 Fase A: extract and upload images from crawl4ai
    # media.images. Skipped silently when garage_s3_endpoint is empty.
    image_store = _build_image_store()
    if image_store is not None:
        media_images = (result.media or {}).get("images") or []
        if media_images:
            try:
                timeout = settings.image_download_timeout
                async with httpx.AsyncClient(timeout=timeout) as http_client:
                    image_urls = await download_and_upload_crawl_images(
                        media_images=media_images,
                        base_url=url,
                        org_id=org_id,
                        kb_slug=kb_slug,
                        image_store=image_store,
                        http_client=http_client,
                    )
                if image_urls:
                    extra["image_urls"] = image_urls
            except Exception as exc:
                logger.warning("crawl_image_upload_failed", url=url, error=str(exc))

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
