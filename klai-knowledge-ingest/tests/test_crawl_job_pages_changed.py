"""A crawl job records how many pages actually changed the knowledge base.

The portal reanalyses support cases (several LLM calls per case) only after a
sync that changed knowledge. ``pages_done`` cannot answer that: it also counts
pages that were skipped as unchanged. ``pages_changed`` counts pages that were
really (re)ingested plus stale pages retired at the end of the crawl.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.crawl4ai_client import CrawlResult
from tests.conftest import connection_factory_for


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _result(url: str, text: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html=f"<html><body><p>{text}</p></body></html>",
        word_count=len(text.split()),
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


def _changed_increments(conn: MagicMock) -> int:
    total = 0
    for call in conn.execute.await_args_list:
        match = call.args and re.search(r"pages_changed=pages_changed\+\$(\d+)", call.args[0])
        if match:
            total += call.args[int(match.group(1))]
    return total


@pytest.mark.asyncio
async def test_crawl_counts_ingested_pages_and_retired_stale_pages_but_not_unchanged_ones() -> None:
    conn = _mock_conn()
    results = [
        _result("https://example.com/a", "Installing the desk phone takes three steps."),
        _result("https://example.com/b", "Voicemail greetings can be recorded from any handset."),
        _result("https://example.com/c", "Call forwarding rules apply per extension and slot."),
    ]
    fetch_outcomes = [{"url": r.url, "reason_code": "success", "status_code": 200} for r in results]
    ingest_answers = iter(
        [
            {"status": "ok", "chunks": 1},
            {"status": "skipped", "reason": "content unchanged", "chunks": 0},
            {"status": "ok", "chunks": 2},
        ]
    )

    async def _ingest(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return next(ingest_answers)

    pg = MagicMock()
    pg.get_crawled_page_hashes = AsyncMock(return_value={})
    pg.get_crawled_page_stored = AsyncMock(return_value=None)
    pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    pg.upsert_crawled_page = AsyncMock()
    pg.update_crawled_page_simhash = AsyncMock()
    pg.upsert_page_links = AsyncMock()
    pg.list_stale_connector_artifact_paths = AsyncMock(return_value=["https://example.com/old"])
    pg.soft_delete_stale_connector_artifacts = AsyncMock(return_value=1)

    with (
        patch("knowledge_ingest.adapters.crawler._update_job", new_callable=AsyncMock),
        patch("knowledge_ingest.adapters.crawler.pg_store", pg),
        patch(
            "knowledge_ingest.adapters.crawler.qdrant_store.delete_document",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
            return_value=(results, fetch_outcomes),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._enqueue_taxonomy_backfill_after_crawl",
            new_callable=AsyncMock,
        ),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
        patch("knowledge_ingest.routes.ingest.ingest_document", side_effect=_ingest),
    ):
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=1,
            rate_limit=100.0,
            connector_id="conn-1",
        )

    pg.soft_delete_stale_connector_artifacts.assert_awaited_once()
    assert _changed_increments(conn) == 3
