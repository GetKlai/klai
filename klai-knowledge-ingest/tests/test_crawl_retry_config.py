"""Retry configuration for deploy-interrupted crawl jobs."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

from knowledge_ingest import crawl_tasks, queues
from knowledge_ingest.crawl_checkpoint import CrawlExecutionBusy


class _RecordingRetryStrategy:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeApp:
    def __init__(self) -> None:
        self.task_kwargs: list[dict[str, Any]] = []
        self.task_functions: list[Any] = []

    def task(self, **kwargs: Any):
        self.task_kwargs.append(kwargs)

        def _decorator(fn):
            self.task_functions.append(fn)
            return fn

        return _decorator


def test_crawl_retries_shutdown_cancellation_and_busy_execution_lock(monkeypatch) -> None:
    monkeypatch.setattr(sys.modules["procrastinate"], "RetryStrategy", _RecordingRetryStrategy)
    app = _FakeApp()

    crawl_tasks.register_crawl_tasks(app)

    retry = app.task_kwargs[0]["retry"]
    assert app.task_kwargs[0]["queue"] == queues.CRAWL_JOBS
    assert retry.kwargs["max_attempts"] is None
    assert retry.kwargs["wait"] == 5
    assert tuple(retry.kwargs["retry_exceptions"]) == (
        asyncio.CancelledError,
        CrawlExecutionBusy,
    )


async def test_crawl_task_does_not_pin_tenant_connection_for_whole_job(monkeypatch) -> None:
    app = _FakeApp()
    connection_depth = 0

    @asynccontextmanager
    async def tenant_connection(_org_id: str):
        nonlocal connection_depth
        connection_depth += 1
        try:
            yield object()
        finally:
            connection_depth -= 1

    async def run_job(*, connection_factory, **_kwargs) -> None:
        assert connection_depth == 0
        async with connection_factory() as _conn:
            assert connection_depth == 1
        assert connection_depth == 0

    monkeypatch.setattr(crawl_tasks, "tenant_scoped_connection", tenant_connection)
    monkeypatch.setattr(
        "knowledge_ingest.adapters.crawler.run_crawl_job", AsyncMock(side_effect=run_job)
    )
    crawl_tasks.register_crawl_tasks(app)

    await app.task_functions[0](
        job_id="job-1",
        org_id="org-1",
        kb_slug="kb-1",
        start_url="https://example.com",
        max_depth=2,
    )

    assert connection_depth == 0


async def test_crawl_tasks_preserve_database_capacity_under_concurrency(monkeypatch) -> None:
    app = _FakeApp()
    active_connections = 0
    peak_connections = 0

    @asynccontextmanager
    async def tenant_connection(_org_id: str):
        nonlocal active_connections, peak_connections
        active_connections += 1
        peak_connections = max(peak_connections, active_connections)
        try:
            yield object()
        finally:
            active_connections -= 1

    async def run_job(*, connection_factory, **_kwargs) -> None:
        async with connection_factory() as _conn:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(crawl_tasks, "tenant_scoped_connection", tenant_connection)
    monkeypatch.setattr(
        "knowledge_ingest.adapters.crawler.run_crawl_job", AsyncMock(side_effect=run_job)
    )
    crawl_tasks.register_crawl_tasks(app)

    await asyncio.gather(
        *(
            app.task_functions[0](
                job_id=f"job-{index}",
                org_id="org-1",
                kb_slug="kb-1",
                start_url="https://example.com",
                max_depth=2,
            )
            for index in range(8)
        )
    )

    assert peak_connections == 4
