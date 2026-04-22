"""
Tests for _build_link_graph helper in adapters/crawler.py (SPEC-CRAWLER-005).

Phase 1 of the two-phase crawl pipeline: build the full link graph BEFORE
any per-page ingest begins. This guarantees get_anchor_texts() and
get_incoming_count() return final values during Phase 2.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.crawl4ai_client import CrawlResult


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_crawl_result(
    url: str = "https://example.com/a",
    success: bool = True,
    links: dict | None = None,
) -> CrawlResult:
    text = "Some markdown content"
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html="<html><body><p>Test</p></body></html>",
        word_count=3,
        success=success,
        links=links if links is not None else {"internal": []},
        error_message="" if success else "Crawl failed",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


@pytest.mark.asyncio
async def test_build_link_graph_calls_upsert_for_each_result_with_internal_links():
    """_build_link_graph calls upsert_page_links once per successful result with internal links."""
    results = [
        _make_crawl_result(
            url=f"https://example.com/page-{i}",
            links={"internal": [{"href": f"https://example.com/page-{i+1}", "text": f"Link {i}"}]},
        )
        for i in range(3)
    ]
    mock_pool = _make_mock_pool()

    with patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg:
        mock_pg.upsert_page_links = AsyncMock()

        from knowledge_ingest.adapters.crawler import _build_link_graph

        await _build_link_graph(results, "org-1", "kb-slug", mock_pool)

    assert mock_pg.upsert_page_links.call_count == 3
    # Verify correct from_url and links for each call
    calls = mock_pg.upsert_page_links.call_args_list
    called_urls = {call.kwargs["from_url"] for call in calls}
    assert called_urls == {
        "https://example.com/page-0",
        "https://example.com/page-1",
        "https://example.com/page-2",
    }
    for call in calls:
        assert call.kwargs["org_id"] == "org-1"
        assert call.kwargs["kb_slug"] == "kb-slug"
        assert len(call.kwargs["links"]) == 1
        assert "href" in call.kwargs["links"][0]


@pytest.mark.asyncio
async def test_build_link_graph_skips_failed_results():
    """_build_link_graph skips results where success=False."""
    results = [
        _make_crawl_result(
            url="https://example.com/ok",
            success=True,
            links={"internal": [{"href": "https://example.com/b", "text": "B"}]},
        ),
        _make_crawl_result(
            url="https://example.com/fail",
            success=False,
            links={"internal": [{"href": "https://example.com/c", "text": "C"}]},
        ),
        _make_crawl_result(
            url="https://example.com/ok2",
            success=True,
            links={"internal": [{"href": "https://example.com/d", "text": "D"}]},
        ),
    ]
    mock_pool = _make_mock_pool()

    with patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg:
        mock_pg.upsert_page_links = AsyncMock()

        from knowledge_ingest.adapters.crawler import _build_link_graph

        await _build_link_graph(results, "org-1", "kb-slug", mock_pool)

    # Only 2 successful results should trigger upsert
    assert mock_pg.upsert_page_links.call_count == 2
    called_urls = {call.kwargs["from_url"] for call in mock_pg.upsert_page_links.call_args_list}
    assert "https://example.com/fail" not in called_urls
    assert "https://example.com/ok" in called_urls
    assert "https://example.com/ok2" in called_urls


@pytest.mark.asyncio
async def test_build_link_graph_skips_empty_internal_links():
    """_build_link_graph skips results with empty or absent internal links."""
    results = [
        # No links key at all (empty dict)
        _make_crawl_result(
            url="https://example.com/no-links",
            links={},
        ),
        # links present but internal is empty list
        _make_crawl_result(
            url="https://example.com/empty-internal",
            links={"internal": []},
        ),
        # links only has external
        _make_crawl_result(
            url="https://example.com/external-only",
            links={"external": [{"href": "https://other.com/page", "text": "Other"}]},
        ),
    ]
    mock_pool = _make_mock_pool()

    with patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg:
        mock_pg.upsert_page_links = AsyncMock()

        from knowledge_ingest.adapters.crawler import _build_link_graph

        await _build_link_graph(results, "org-1", "kb-slug", mock_pool)

    # No upsert calls — no result has non-empty internal links
    mock_pg.upsert_page_links.assert_not_called()


@pytest.mark.asyncio
async def test_build_link_graph_no_qdrant_no_ingest():
    """_build_link_graph never touches Qdrant or ingest_document."""
    results = [
        _make_crawl_result(
            url="https://example.com/a",
            links={"internal": [{"href": "https://example.com/b", "text": "B"}]},
        ),
    ]
    mock_pool = _make_mock_pool()

    with (
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
        ) as mock_crawl,
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        mock_pg.upsert_page_links = AsyncMock()

        from knowledge_ingest.adapters.crawler import _build_link_graph

        await _build_link_graph(results, "org-1", "kb-slug", mock_pool)

    # crawl_site should not be called from _build_link_graph
    mock_crawl.assert_not_called()
    # ingest_document should not be called from _build_link_graph
    mock_ingest.assert_not_called()
