"""Procrastinate task for async bulk web crawling."""
from __future__ import annotations

from typing import Any


def register_crawl_tasks(procrastinate_app: Any) -> None:
    """Register crawl tasks on the Procrastinate app. Called from enrichment_tasks.init_app()."""
    import procrastinate  # noqa: PLC0415

    @procrastinate_app.task(queue="enrich-bulk", retry=procrastinate.RetryStrategy(max_attempts=1))
    async def run_crawl(
        job_id: str,
        org_id: str,
        kb_slug: str,
        start_url: str,
        max_depth: int,
        include_patterns: list[str] | None,
        exclude_patterns: list[str] | None,
        rate_limit: float,
        content_selector: str | None = None,
    ) -> None:
        from knowledge_ingest.adapters.crawler import run_crawl_job  # noqa: PLC0415
        await run_crawl_job(
            job_id=job_id,
            org_id=org_id,
            kb_slug=kb_slug,
            start_url=start_url,
            max_depth=max_depth,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            rate_limit=rate_limit,
            content_selector=content_selector,
        )

    procrastinate_app.run_crawl = run_crawl  # type: ignore[attr-defined]
