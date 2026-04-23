"""Procrastinate task for async bulk web crawling."""
from __future__ import annotations

from typing import Any


def register_crawl_tasks(procrastinate_app: Any) -> None:
    """Register crawl tasks on the Procrastinate app. Called from enrichment_tasks.init_app()."""
    import procrastinate

    @procrastinate_app.task(queue="enrich-bulk", retry=procrastinate.RetryStrategy(max_attempts=1))
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
        canary_url: str | None = None,
        canary_fingerprint: str | None = None,
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
        await run_crawl_job(
            job_id=job_id,
            org_id=org_id,
            kb_slug=kb_slug,
            start_url=start_url,
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
        )

    procrastinate_app.run_crawl = run_crawl  # type: ignore[attr-defined]
