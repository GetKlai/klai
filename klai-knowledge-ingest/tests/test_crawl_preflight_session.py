"""The session check before a crawl: a lost login fails the pending job and fetches nothing."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest import crawl_tasks
from knowledge_ingest.routes import crawl_sync

CONTENT = "wiki.example.com"
PAGE = f"https://{CONTENT}/nl/handbook/getting-started"
CONNECTOR = uuid.UUID("00000000-0000-4000-8000-000000000001")
ORG = "42"


class _JobRows:
    """Stands in for the tenant-scoped connection the pre-flight fails a job on."""

    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []
        self.status = "pending"

    def factory(self, _org_id: str):
        rows = self

        class _Conn:
            async def fetchval(self, _sql: str, _job_id: str) -> str:
                return rows.status

            async def execute(self, _sql: str, job_id: str, error: str) -> str:
                rows.failed.append((job_id, error))
                return "UPDATE 1"

        class _Ctx:
            async def __aenter__(self) -> _Conn:
                return _Conn()

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Ctx()


async def _preflight(rows: _JobRows) -> bool:
    with patch.object(crawl_tasks, "_crawl_db_connection", rows.factory):
        return await crawl_tasks.preflight_session(
            job_id="job-1",
            org_id=ORG,
            connector_id=str(CONNECTOR),
            url=PAGE,
        )


@pytest.mark.asyncio
async def test_a_crawl_whose_login_is_gone_fetches_nothing() -> None:
    class _App:
        run_crawl: Any = None

        def task(self, **_kwargs: Any):
            def _register(fn):
                self.run_crawl = fn
                return fn

            return _register

    app = _App()
    crawl_tasks.register_crawl_tasks(app)
    rows = _JobRows()
    run_job = AsyncMock()
    verdict = crawl_sync.CrawlKeepAliveResponse(
        ok=False, reason="login_required:embedded_login_gate", login_required=True
    )
    with (
        patch.object(crawl_tasks, "_crawl_db_connection", rows.factory),
        patch(
            "knowledge_ingest.routes.crawl_sync.keep_session", AsyncMock(return_value=verdict)
        ) as check,
        patch("knowledge_ingest.adapters.crawler.run_crawl_job", run_job),
    ):
        await app.run_crawl(
            job_id="job-1",
            org_id=ORG,
            kb_slug="kb",
            start_url=f"https://{CONTENT}/nl/",
            max_depth=2,
            connector_id=str(CONNECTOR),
        )
    assert check.await_args is not None
    assert check.await_args.kwargs["url"] == f"https://{CONTENT}/nl/"
    run_job.assert_not_awaited()
    assert rows.failed == [("job-1", verdict.reason)]


@pytest.mark.asyncio
async def test_a_crawl_another_execution_already_runs_is_not_checked_again() -> None:
    rows = _JobRows()
    rows.status = "running"
    check = AsyncMock()
    with patch("knowledge_ingest.routes.crawl_sync.keep_session", check):
        assert await _preflight(rows) is True
    check.assert_not_awaited()
