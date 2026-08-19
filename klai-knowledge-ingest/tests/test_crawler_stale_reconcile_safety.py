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
