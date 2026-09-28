"""Stale-connector-artifact reconciliation must never fire on an incomplete crawl.

Damage 1 (stop-the-bleeding fix): ``run_crawl_job``'s stale-artifact
reconciliation compared ``current_paths`` (only the URLs that were
successfully fetched THIS run) against everything on record for the
connector, and deleted anything missing from ``current_paths`` — including
Qdrant vectors and the Postgres artifact row.

A URL that merely timed out (or hit any other real fetch failure) this run
is absent from ``results`` for a reason that has nothing to do with the URL
still existing on the site. Because ``decide_fetch_failure_terminal_status``
tolerates up to a 30% fetch-failure rate before flipping the job away from
``completed``, a crawl with real failures under that threshold reached the
reconciliation block and retired live knowledge as "stale".

The fix: skip the whole reconciliation whenever the job saw ANY real fetch
failure or ``not_fetched_*`` outcome. Only a crawl where every discovered
URL was actually fetched may conclude that a missing path is gone.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import crawl4ai_client, link_graph
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


def _make_crawl_result(url: str) -> CrawlResult:
    text = "Some real page content for testing purposes."
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html="<html><body><p>Test content</p></body></html>",
        word_count=len(text.split()),
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


def _patch_common(mock_pg, results, fetch_outcomes, ingest_side_effect):
    return [
        patch(
            "knowledge_ingest.adapters.crawler._update_job",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch(
            "knowledge_ingest.adapters.crawler.qdrant_store.delete_document",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
            return_value=(results, fetch_outcomes),
        ),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
        patch("knowledge_ingest.routes.ingest.ingest_document", side_effect=ingest_side_effect),
    ]


@pytest.mark.asyncio
async def test_stale_reconcile_skipped_when_any_page_timed_out():
    """A job that ``completed`` despite one real fetch failure MUST NOT
    delete stale artifacts.

    4 pages fetched successfully, 1 timed out (20% failure rate — safely
    under the 30% dirty-trip threshold, so the job legitimately reaches
    ``completed``). Before the fix, the timed-out URL's existing artifact
    would be treated as stale and deleted from both Qdrant and Postgres.
    """
    mock_conn = _make_mock_conn()
    results = [
        _make_crawl_result(f"https://example.com/{letter}") for letter in ("a", "b", "c", "d")
    ]
    fetch_outcomes = [{"url": r.url, "reason_code": "success", "status_code": 200} for r in results]
    fetch_outcomes.append(
        {"url": "https://example.com/e", "reason_code": "timeout", "status_code": None}
    )

    async def _fake_ingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"chunks": 1}

    mock_pg = MagicMock()
    mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
    mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
    mock_pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    mock_pg.upsert_crawled_page = AsyncMock()
    mock_pg.update_crawled_page_simhash = AsyncMock()
    mock_pg.upsert_page_links = AsyncMock()
    # If reconciliation were to run, it would report /e as stale (it has an
    # existing artifact but is absent from this run's `current_paths`).
    mock_pg.list_stale_connector_artifact_paths = AsyncMock(
        return_value=["https://example.com/e"],
    )
    mock_pg.soft_delete_stale_connector_artifacts = AsyncMock(return_value=1)

    patches = _patch_common(mock_pg, results, fetch_outcomes, _fake_ingest)
    for p in patches:
        p.start()
    try:
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
            connector_id="conn-1",
        )
    finally:
        for p in patches:
            p.stop()

    # The core assertion: an incomplete crawl (any real fetch failure) must
    # never even ask "what's stale" — let alone delete anything.
    mock_pg.list_stale_connector_artifact_paths.assert_not_awaited()
    mock_pg.soft_delete_stale_connector_artifacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_reconcile_still_runs_when_crawl_is_fully_fetched():
    """The safety guard must not silently disable reconciliation altogether.

    A crawl with zero fetch failures and zero not_fetched_* outcomes is
    demonstrably complete, so reconciliation must still run — otherwise
    genuinely removed pages would never be cleaned up.
    """
    mock_conn = _make_mock_conn()
    results = [_make_crawl_result("https://www.getklai.com/")]
    fetch_outcomes = [{"url": results[0].url, "reason_code": "success", "status_code": 200}]

    async def _fake_ingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"chunks": 1}

    mock_pg = MagicMock()
    mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
    mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
    mock_pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    mock_pg.upsert_crawled_page = AsyncMock()
    mock_pg.update_crawled_page_simhash = AsyncMock()
    mock_pg.upsert_page_links = AsyncMock()
    mock_pg.list_stale_connector_artifact_paths = AsyncMock(
        return_value=["https://getklai.com/"],
    )
    mock_pg.soft_delete_stale_connector_artifacts = AsyncMock(return_value=1)

    patches = _patch_common(mock_pg, results, fetch_outcomes, _fake_ingest)
    for p in patches:
        p.start()
    try:
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="klai-web-demo",
            start_url="https://www.getklai.com/",
            max_depth=1,
            rate_limit=100.0,
            connector_id="conn-1",
        )
    finally:
        for p in patches:
            p.stop()

    mock_pg.list_stale_connector_artifact_paths.assert_awaited_once()
    mock_pg.soft_delete_stale_connector_artifacts.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "retired"), [(404, True), (410, True), (401, False)])
async def test_page_that_is_gone_lets_stale_cleanup_retire_it_but_an_auth_error_does_not(
    status: int, retired: bool
):
    """A 404/410 answer means the page no longer exists, so a crawl that is
    otherwise complete must still run the stale cleanup and retire the
    stored version. A 401 says nothing about the page being gone: the
    stored version stays and the cleanup is skipped."""
    mock_conn = _make_mock_conn()
    results = [_make_crawl_result(f"https://example.com/{letter}") for letter in ("a", "b", "c")]
    fetch_outcomes = [{"url": r.url, "reason_code": "success", "status_code": 200} for r in results]
    removed = "https://example.com/removed"
    # crawl4ai 0.9.4 reports a full-size error page as success=True.
    page = {
        "url": removed,
        "success": True,
        "status_code": status,
        "redirected_status_code": status,
        "html": "<html><body>" + "Page not available. " * 50 + "</body></html>",
        "markdown": "Page not available. " * 50,
        "links": {"internal": []},
    }
    error_page_results, error_page_outcomes = crawl4ai_client._combine_bulk_responses(
        candidates=[removed],
        raw_results=crawl4ai_client._normalise_results_block({"results": [page]}),
        transport_error=None,
        base_domain="example.com",
    )
    assert error_page_results == []
    fetch_outcomes += error_page_outcomes

    async def _fake_ingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"chunks": 1}

    mock_pg = MagicMock()
    mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
    mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
    mock_pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    mock_pg.upsert_crawled_page = AsyncMock()
    mock_pg.update_crawled_page_simhash = AsyncMock()
    mock_pg.upsert_page_links = AsyncMock()
    mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=[removed])
    mock_pg.count_active_connector_artifact_paths = AsyncMock(return_value=4)
    mock_pg.soft_delete_stale_connector_artifacts = AsyncMock(return_value=1)

    patches = _patch_common(mock_pg, results, fetch_outcomes, _fake_ingest)
    for p in patches:
        p.start()
    try:
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
            connector_id="conn-1",
        )
    finally:
        for p in patches:
            p.stop()

    if retired:
        current = mock_pg.list_stale_connector_artifact_paths.await_args.kwargs["current_paths"]
        assert removed not in current
        stale = mock_pg.soft_delete_stale_connector_artifacts.await_args.kwargs["stale_paths"]
        assert stale == [removed]
    else:
        mock_pg.list_stale_connector_artifact_paths.assert_not_awaited()
        mock_pg.soft_delete_stale_connector_artifacts.assert_not_awaited()


def _pg_mock(stale_paths: list[str], stored_count: int) -> MagicMock:
    mock_pg = MagicMock()
    mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
    mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
    mock_pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    mock_pg.upsert_crawled_page = AsyncMock()
    mock_pg.update_crawled_page_simhash = AsyncMock()
    mock_pg.upsert_page_links = AsyncMock()
    mock_pg.list_stale_connector_artifact_paths = AsyncMock(return_value=stale_paths)
    mock_pg.count_active_connector_artifact_paths = AsyncMock(return_value=stored_count)
    mock_pg.soft_delete_stale_connector_artifacts = AsyncMock(return_value=len(stale_paths))
    return mock_pg


async def _run_job(mock_pg, results, fetch_outcomes, **kwargs):  # type: ignore[no-untyped-def]
    async def _fake_ingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"chunks": 1}

    mock_conn = _make_mock_conn()
    patches = _patch_common(mock_pg, results, fetch_outcomes, _fake_ingest)
    for p in patches:
        p.start()
    try:
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(mock_conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
            connector_id="conn-1",
            **kwargs,
        )
    finally:
        for p in patches:
            p.stop()
    return mock_conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale_count", "stored_count", "retired"),
    [
        # 6 > max(5, 20% of 10): a 404 hub or an SPA answering 404 would
        # retire most of a small source in one run.
        (6, 10, False),
        # 30 > max(5, 20% of 100).
        (30, 100, False),
        # 5 == the absolute floor: a small source can still drop a handful.
        (5, 10, True),
        # 20 == 20% of 100.
        (20, 100, True),
    ],
)
async def test_crawl_with_gone_pages_does_not_bulk_retire_the_stored_source(
    stale_count: int, stored_count: int, retired: bool
):
    results = [_make_crawl_result("https://example.com/a")]
    fetch_outcomes = [
        {"url": "https://example.com/a", "reason_code": "success", "status_code": 200},
        {"url": "https://example.com/hub", "reason_code": "gone", "status_code": 404},
    ]
    stale = [f"https://example.com/old/{i}" for i in range(stale_count)]
    mock_pg = _pg_mock(stale, stored_count)

    await _run_job(mock_pg, results, fetch_outcomes)

    mock_pg.list_stale_connector_artifact_paths.assert_awaited_once()
    assert mock_pg.soft_delete_stale_connector_artifacts.await_count == int(retired)


@pytest.mark.asyncio
async def test_crawl_without_gone_pages_keeps_retiring_every_stale_page():
    results = [_make_crawl_result("https://example.com/a")]
    fetch_outcomes = [
        {"url": "https://example.com/a", "reason_code": "success", "status_code": 200}
    ]
    stale = [f"https://example.com/old/{i}" for i in range(30)]
    mock_pg = _pg_mock(stale, 31)

    await _run_job(mock_pg, results, fetch_outcomes)

    assert mock_pg.soft_delete_stale_connector_artifacts.await_args.kwargs["stale_paths"] == stale


@pytest.mark.asyncio
async def test_401_pages_feed_login_wall_accounting_not_fetch_failure_dominant():
    """An expired session answers 401: the pages are not ingested and block
    the stale cleanup, but they are login walls (content_wall_signal_*
    events, crawl_job_auth_wall_rate_high), not a fetch-failure trip."""
    from structlog.testing import capture_logs

    results = [_make_crawl_result(f"https://example.com/{c}") for c in "ab"]
    fetch_outcomes = [
        {"url": r.url, "reason_code": "success", "status_code": 200} for r in results
    ] + [
        crawl4ai_client._combine_bulk_responses(
            candidates=[url],
            raw_results=crawl4ai_client._normalise_results_block(
                {"results": [{"url": url, "success": True, "status_code": 401, "html": "x"}]}
            ),
            transport_error=None,
            base_domain="example.com",
        )[1][0]
        for url in (f"https://example.com/private-{i}" for i in range(2))
    ]
    mock_pg = _pg_mock([], 2)

    with capture_logs() as logs:
        mock_conn = await _run_job(
            mock_pg, results, fetch_outcomes, cookies=[{"name": "s", "value": "x"}]
        )

    events = [log["event"] for log in logs]
    assert events.count("content_wall_signal_reject") == 2
    assert "crawl_job_auth_wall_rate_high" in events
    written = " ".join(str(call.args) for call in mock_conn.execute.call_args_list)
    assert "fetch_failure_dominant" not in written
    mock_pg.list_stale_connector_artifact_paths.assert_not_awaited()
