"""crawl-cancel — ``run_crawl_job`` terminal status when a crawl is cancelled.

``crawl_site`` reports a mid-crawl cancellation to its caller purely via
``FetchReasonCode.NOT_FETCHED_CANCELLED`` entries in the returned
``fetch_outcomes`` — no third return value, so every existing caller/test
of ``crawl_site`` keeps working unchanged. ``run_crawl_job`` detects that
reason code and must:

- still ingest whatever pages were fetched before the cancel (no data loss),
- end with ``crawl_jobs.status = 'cancelled'`` — never ``'failed'`` or
  ``'failed_partial'`` — a user-requested stop is not a failure,
- skip the discovery-seed retry pass entirely once cancelled (no point
  starting a second crawl for a job that was just told to stop).

Mirrors the mocking pattern in test_crawl_job_ingest_failure_status.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.adapters.crawler import _update_job, run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode
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
    """Same helper as test_crawl_job_ingest_failure_status.py: the ``status``
    argument of the LAST ``crawl_jobs`` status UPDATE issued on ``mock_conn``."""
    status_calls = [
        call for call in mock_conn.execute.call_args_list if "SET status=$1" in call.args[0]
    ]
    if not status_calls:
        return None
    return status_calls[-1].args[1]


@pytest.mark.asyncio
async def test_cancel_before_claim_cannot_overwrite_a_terminal_job() -> None:
    conn = _make_mock_conn()

    await _update_job(conn, "job-1")

    query = conn.execute.await_args.args[0]
    assert "cancel_requested=true" in query
    assert "status IN ('pending', 'running')" in query


@pytest.mark.asyncio
async def test_cancelled_mid_crawl_reports_status_cancelled_not_failed() -> None:
    """One page already fetched, the rest cancelled — status must be
    'cancelled', not 'completed', 'failed', or 'failed_partial'."""
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
        ) as mock_ingest,
    ):
        mock_crawl.return_value = (
            [mock_result],
            [
                {
                    "url": mock_result.url,
                    "reason_code": FetchReasonCode.SUCCESS.value,
                    "status_code": 200,
                    "content_length": len(mock_result.html or ""),
                },
                {
                    "url": "https://example.com/b",
                    "reason_code": FetchReasonCode.NOT_FETCHED_CANCELLED.value,
                    "status_code": None,
                    "content_length": 0,
                },
                {
                    "url": "https://example.com/c",
                    "reason_code": FetchReasonCode.NOT_FETCHED_CANCELLED.value,
                    "status_code": None,
                    "content_length": 0,
                },
            ],
        )
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[])

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
    assert status == "cancelled", f"expected 'cancelled', got {status!r}"
    # The page fetched before cancellation must still be ingested — no data
    # loss on a cooperative stop.
    mock_ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_skips_discovery_seed_retry() -> None:
    """A cancelled primary crawl must not trigger the discovery-seed retry
    pass — crawl_site is called exactly once, not twice."""
    mock_conn = _make_mock_conn()
    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()

    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new_callable=AsyncMock) as mock_crawl,
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
    ):
        # Zero pages fetched, discovery_seed_url never reached — normally
        # this alone would trigger the seed-retry crawl_site call, but the
        # job was cancelled before anything was fetched at all.
        mock_crawl.return_value = (
            [],
            [
                {
                    "url": "https://example.com/a",
                    "reason_code": FetchReasonCode.NOT_FETCHED_CANCELLED.value,
                    "status_code": None,
                    "content_length": 0,
                },
            ],
        )
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[])

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            discovery_seed_url="https://example.com/known-good",
            max_depth=1,
            rate_limit=100.0,
        )

    assert mock_crawl.await_count == 1, (
        f"expected crawl_site to be called once (no seed retry after cancel), "
        f"got {mock_crawl.await_count}"
    )
    status = _last_status_call(mock_conn)
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_no_cancellation_reason_code_still_reports_completed() -> None:
    """Regression / counterpart: a normal, non-cancelled crawl is unaffected
    by the new job_cancelled detection — matches the existing happy-path
    test in test_crawl_job_ingest_failure_status.py."""
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
                    "reason_code": FetchReasonCode.SUCCESS.value,
                    "status_code": 200,
                    "content_length": len(mock_result.html or ""),
                }
            ],
        )
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[])

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
