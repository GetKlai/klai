"""Tests for Deel B: an ingest failure must not report ``completed`` (SPEC-
CRAWLER-FAILURE-EVIDENCE).

``run_crawl_job`` increments ``pages_failed`` on every ``_ingest_crawl_result``
exception (embedding provider down, database gone, ...) but never reads that
counter again — a job where every single ingest attempt failed still ends
with ``status="completed"``. This was reproduced with a harness: one
successful fetch + a ``RuntimeError`` from ``_ingest_crawl_result`` yields
``pages_done=0 pages_failed=1 status=completed``.

Scope for this fix (explicitly NOT the full materialization accounting):
a job in which EVERY ingest attempt failed (``pages_done == 0 and
pages_failed > 0``) must never be reported as ``completed``. Partial-failure
tolerance (a ratio threshold) is deliberately out of scope here — see
``decide_ingest_failure_terminal_status``'s docstring for the rationale.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.crawl4ai_client import CrawlResult
from tests.conftest import connection_factory_for


def _make_mock_conn():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _make_crawl_result(url: str = "https://example.com/a") -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown="Some markdown content for testing",
        raw_markdown="Some markdown content for testing",
        html="<html><body><p>Test content</p></body></html>",
        word_count=5,
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


def _last_status_call(mock_conn: MagicMock) -> str | None:
    """Return the ``status`` argument of the LAST ``crawl_jobs`` status
    UPDATE issued on ``mock_conn`` — every branch in ``run_crawl_job``'s
    terminal-status chain writes ``SET status=$1, ...`` with the status as
    the first bound parameter, whether via ``_update_job`` or a direct
    ``conn.execute`` call."""
    status_calls = [
        call for call in mock_conn.execute.call_args_list if "SET status=$1" in call.args[0]
    ]
    if not status_calls:
        return None
    return status_calls[-1].args[1]


@pytest.mark.asyncio
async def test_job_with_total_ingest_failure_is_not_reported_completed() -> None:
    """One successful fetch, its ingest raises — pages_done=0, pages_failed=1.
    The job MUST NOT be reported as completed."""
    mock_conn = _make_mock_conn()
    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    mock_result = _make_crawl_result()

    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new_callable=AsyncMock) as mock_crawl,
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embedding provider unreachable"),
        ),
    ):
        mock_crawl.return_value = (
            [mock_result],
            [
                {
                    "url": mock_result.url,
                    "reason_code": "success",
                    "status_code": 200,
                    "content_length": len(mock_result.html or ""),
                }
            ],
        )
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[])

        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
        )

    status = _last_status_call(mock_conn)
    assert status is not None, "no crawl_jobs status UPDATE was issued at all"
    assert status != "completed", (
        f"job with pages_done=0 pages_failed=1 (every ingest failed) reported "
        f"status={status!r} — a total ingest failure must never be 'completed'"
    )


@pytest.mark.asyncio
async def test_job_with_all_ingests_succeeding_is_still_completed() -> None:
    """Counterpart: the new guard must not break the existing happy path."""
    mock_conn = _make_mock_conn()
    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    mock_result = _make_crawl_result()

    async def _fake_ingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new_callable=AsyncMock) as mock_crawl,
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new_callable=AsyncMock,
            side_effect=_fake_ingest,
        ),
    ):
        mock_crawl.return_value = (
            [mock_result],
            [
                {
                    "url": mock_result.url,
                    "reason_code": "success",
                    "status_code": 200,
                    "content_length": len(mock_result.html or ""),
                }
            ],
        )
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[])

        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
        )

    status = _last_status_call(mock_conn)
    assert status == "completed"


# ---------------------------------------------------------------------------
# decide_ingest_failure_terminal_status — pure decision function
# ---------------------------------------------------------------------------


class TestDecideIngestFailureTerminalStatus:
    def test_total_ingest_failure_trips_the_guard(self) -> None:
        from knowledge_ingest.adapters.crawler import decide_ingest_failure_terminal_status

        status, summary = decide_ingest_failure_terminal_status(pages_done=0, pages_failed=3)
        assert status == "failed_partial"
        assert summary is not None
        assert summary["pages_failed"] == 3
        assert summary["pages_attempted"] == 3

    def test_partial_failure_does_not_trip_the_guard(self) -> None:
        """Explicitly out of scope for this fix — at least one page ingested
        successfully means the guard does not fire (no ratio tolerance
        introduced here)."""
        from knowledge_ingest.adapters.crawler import decide_ingest_failure_terminal_status

        status, summary = decide_ingest_failure_terminal_status(pages_done=1, pages_failed=5)
        assert status == ""
        assert summary is None

    def test_no_ingest_attempts_does_not_trip_the_guard(self) -> None:
        """No fetches reached the ingest loop at all — a different guard
        (fetch-failure / crawl-outcome-warning) owns that case."""
        from knowledge_ingest.adapters.crawler import decide_ingest_failure_terminal_status

        status, summary = decide_ingest_failure_terminal_status(pages_done=0, pages_failed=0)
        assert status == ""
        assert summary is None

    def test_all_succeeded_does_not_trip_the_guard(self) -> None:
        from knowledge_ingest.adapters.crawler import decide_ingest_failure_terminal_status

        status, summary = decide_ingest_failure_terminal_status(pages_done=4, pages_failed=0)
        assert status == ""
        assert summary is None
