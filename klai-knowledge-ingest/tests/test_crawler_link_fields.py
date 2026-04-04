"""
Tests for link graph field population in crawler adapter (SPEC-CRAWLER-003, TASK-005).

Verifies that _ingest_crawl_result populates extra with link fields,
and that run_crawl_job calls compute_incoming_counts + update_link_counts.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.crawl4ai_client import CrawlResult


def _make_mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_crawl_result(
    url: str = "https://example.com/a",
    success: bool = True,
    text: str = "Some markdown content for testing",
    links: dict | None = None,
) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html="<html><body><p>Test content</p></body></html>",
        word_count=len(text.split()),
        success=success,
        links=links if links is not None else {"internal": []},
        error_message="" if success else "Crawl failed",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


@pytest.mark.asyncio
async def test_ingest_crawl_result_populates_link_fields():
    """_ingest_crawl_result populates extra with links_to, anchor_texts, incoming_link_count."""
    mock_pool = _make_mock_pool()
    result = _make_crawl_result()

    outbound_urls = ["https://example.com/b"]
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch.object(
            link_graph, "get_outbound_urls",
            new_callable=AsyncMock, return_value=outbound_urls,
        ),
        patch.object(
            link_graph, "get_anchor_texts",
            new_callable=AsyncMock, return_value=["Link to B"],
        ),
        patch.object(
            link_graph, "get_incoming_count",
            new_callable=AsyncMock, return_value=3,
        ),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()
        mock_pg.upsert_page_links = AsyncMock()

        mock_ingest.return_value = {"chunks": 2}

        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            result,
            url="https://example.com/a",
            org_id="org-1",
            kb_slug="docs",
            pool=mock_pool,
            stored=None,
        )

        # Verify ingest_document was called with link fields
        mock_ingest.assert_called_once()
        ingest_req = mock_ingest.call_args[0][0]
        assert ingest_req.extra["links_to"] == ["https://example.com/b"]
        assert ingest_req.extra["anchor_texts"] == ["Link to B"]
        assert ingest_req.extra["incoming_link_count"] == 3


@pytest.mark.asyncio
async def test_ingest_crawl_result_graceful_on_link_graph_error():
    """When link_graph raises, _ingest_crawl_result still ingests without link fields."""
    mock_pool = _make_mock_pool()
    result = _make_crawl_result()

    with (
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch.object(
            link_graph, "get_outbound_urls",
            new_callable=AsyncMock, side_effect=Exception("DB down"),
        ),
        patch.object(
            link_graph, "get_anchor_texts",
            new_callable=AsyncMock, return_value=[],
        ),
        patch.object(
            link_graph, "get_incoming_count",
            new_callable=AsyncMock, return_value=0,
        ),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()
        mock_pg.upsert_page_links = AsyncMock()

        mock_ingest.return_value = {"chunks": 1}

        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            result,
            url="https://example.com/a",
            org_id="org-1",
            kb_slug="docs",
            pool=mock_pool,
            stored=None,
        )

        mock_ingest.assert_called_once()
        ingest_req = mock_ingest.call_args[0][0]
        assert ingest_req.extra["source_url"] == "https://example.com/a"
        assert "links_to" not in ingest_req.extra


@pytest.mark.asyncio
async def test_run_crawl_job_calls_compute_and_update_link_counts():
    """After crawl loop, run_crawl_job calls compute_incoming_counts + update_link_counts."""
    mock_pool = _make_mock_pool()
    mock_result = _make_crawl_result(links={"internal": []})

    with (
        patch(
            "knowledge_ingest.adapters.crawler.get_pool",
            new_callable=AsyncMock, return_value=mock_pool,
        ),
        patch(
            "knowledge_ingest.adapters.crawler._update_job",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock, return_value=[mock_result],
        ),
        patch.object(
            link_graph, "get_outbound_urls",
            new_callable=AsyncMock, return_value=[],
        ),
        patch.object(
            link_graph, "get_anchor_texts",
            new_callable=AsyncMock, return_value=[],
        ),
        patch.object(
            link_graph, "get_incoming_count",
            new_callable=AsyncMock, return_value=0,
        ),
        patch.object(
            link_graph, "compute_incoming_counts",
            new_callable=AsyncMock,
        ) as mock_compute,
    ):
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()
        mock_pg.upsert_page_links = AsyncMock()

        mock_compute.return_value = {
            "https://example.com/a": 5,
            "https://example.com/b": 2,
        }

        ingest_patch = patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock, return_value={"chunks": 1},
        )
        with ingest_patch:
            import knowledge_ingest.qdrant_store as qs_mod
            with patch.object(
                qs_mod, "update_link_counts", new_callable=AsyncMock,
            ) as mock_update:
                from knowledge_ingest.adapters.crawler import (
                    run_crawl_job,
                )

                await run_crawl_job(
                    job_id="job-1",
                    org_id="org-1",
                    kb_slug="docs",
                    start_url="https://example.com/a",
                    max_depth=1,
                    rate_limit=100.0,
                )

                mock_compute.assert_called_once_with(
                    "org-1", "docs", mock_pool,
                )
                mock_update.assert_called_once_with(
                    "org-1", "docs",
                    {
                        "https://example.com/a": 5,
                        "https://example.com/b": 2,
                    },
                )
