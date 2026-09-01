"""Procrastinate task for async bulk web crawling."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from knowledge_ingest import queues
from knowledge_ingest.crawl_checkpoint import CrawlExecutionBusy
from knowledge_ingest.db import tenant_scoped_connection

_CRAWL_DB_CONNECTION_LIMIT = 4
_crawl_db_connection_slots = asyncio.Semaphore(_CRAWL_DB_CONNECTION_LIMIT)


@asynccontextmanager
async def _crawl_db_connection(org_id: str) -> AsyncIterator[Any]:
    async with _crawl_db_connection_slots:
        async with tenant_scoped_connection(org_id) as conn:
            yield conn


def register_crawl_tasks(procrastinate_app: Any) -> None:
    """Register crawl tasks on the Procrastinate app. Called from enrichment_tasks.init_app()."""

    import procrastinate

    # SPEC-INGEST-QUEUE-SEPARATION-001: ``run_crawl`` lives on its own
    # ``crawl-jobs`` queue. Was previously sharing ``enrich-bulk`` with the
    # LLM-bound enrichment tasks (Mistral entity/relation extraction takes
    # 30-60s per call). A bulk Notion sync (120 pages → 120 enrichment jobs)
    # would block any subsequent crawl request for 20+ minutes. Crawl is
    # I/O-bound (httpx + crawl4ai), enrichment is LLM-bound — different
    # workloads belong on different queues.
    @procrastinate_app.task(
        queue=queues.CRAWL_JOBS,
        retry=procrastinate.RetryStrategy(
            max_attempts=None,
            wait=5,
            retry_exceptions=(asyncio.CancelledError, CrawlExecutionBusy),
        ),
    )
    async def run_crawl(
        job_id: str,
        org_id: str,
        kb_slug: str,
        start_url: str,
        max_depth: int,
        max_pages: int = 200,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        rate_limit: float = 2.0,
        content_selector: str | None = None,
        login_indicator_selector: str | None = None,
        connector_id: str | None = None,
        resource_key: str | None = None,
        canary_url: str | None = None,
        canary_fingerprint: str | None = None,
        discovery_seed_url: str | None = None,
    ) -> None:
        # REQ-05.4: decrypt cookies at task run-time, not at enqueue-time,
        # so Procrastinate's ``procrastinate_jobs.args`` column and the
        # worker's "Starting job" log line never hold plaintext cookies.
        import uuid as _uuid

        cookies: list[dict] = []
        if connector_id:
            from knowledge_ingest.config import settings
            from knowledge_ingest.connector_cookies import load_connector_cookies
            from knowledge_ingest.db import get_pool

            pool = await get_pool()
            cookies = await load_connector_cookies(
                connector_id=_uuid.UUID(connector_id),
                expected_zitadel_org_id=org_id,
                pool=pool,
                kek_hex=settings.encryption_key,
            )

        from knowledge_ingest.adapters.crawler import run_crawl_job

        # Each database phase leases its own GUC-pinned connection. The crawl's
        # network waits therefore do not reserve one of the ten pool slots,
        # while the crawl-wide limiter preserves capacity for other workloads.
        await run_crawl_job(
            connection_factory=lambda: _crawl_db_connection(org_id),
            job_id=job_id,
            org_id=org_id,
            kb_slug=kb_slug,
            start_url=start_url,
            discovery_seed_url=discovery_seed_url,
            max_depth=max_depth,
            max_pages=max_pages,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            rate_limit=rate_limit,
            content_selector=content_selector,
            login_indicator_selector=login_indicator_selector,
            cookies=cookies,
            canary_url=canary_url,
            canary_fingerprint=canary_fingerprint,
            connector_id=connector_id,
            resource_key=resource_key,
        )

    procrastinate_app.run_crawl = run_crawl  # type: ignore[attr-defined]
