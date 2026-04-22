"""
Tests for pg_store.upsert_page_links.

Covers:
- Relative URL resolution via urljoin
- link_text truncated at 500 characters
- Empty href entries are skipped
- Absolute URLs stored as-is
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.executemany = AsyncMock(return_value=None)
    return pool


def _first_row(mock_pool: MagicMock) -> tuple:
    """Return the first row tuple from the executemany call."""
    rows = mock_pool.executemany.call_args.args[1]
    return rows[0]


@pytest.mark.asyncio
async def test_page_links_relative_url_resolution() -> None:
    """Relative hrefs are resolved against from_url before storing."""
    mock_pool = _make_mock_pool()

    get_pool_patch = patch(
        "knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=mock_pool
    )
    with get_pool_patch:
        from knowledge_ingest.pg_store import upsert_page_links
        await upsert_page_links(
            org_id="org1",
            kb_slug="kb1",
            from_url="https://help.example.com/docs/guide",
            links=[{"href": "../api/overview", "text": "API Overview"}],
        )

    mock_pool.executemany.assert_called_once()
    to_url = _first_row(mock_pool)[3]  # index 3 = to_url
    assert to_url in (
        "https://help.example.com/docs/../api/overview",
        "https://help.example.com/api/overview",
    ), f"Unexpected to_url: {to_url}"


@pytest.mark.asyncio
async def test_page_links_absolute_url_stored_as_is() -> None:
    """Absolute hrefs are stored without modification."""
    mock_pool = _make_mock_pool()

    get_pool_patch = patch(
        "knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=mock_pool
    )
    with get_pool_patch:
        from knowledge_ingest.pg_store import upsert_page_links
        await upsert_page_links(
            org_id="org1",
            kb_slug="kb1",
            from_url="https://help.example.com/docs/guide",
            links=[{"href": "https://other.example.com/page", "text": "External"}],
        )

    to_url = _first_row(mock_pool)[3]
    assert to_url == "https://other.example.com/page"


@pytest.mark.asyncio
async def test_page_links_empty_href_skipped() -> None:
    """Links with empty href are silently skipped."""
    mock_pool = _make_mock_pool()

    get_pool_patch = patch(
        "knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=mock_pool
    )
    with get_pool_patch:
        from knowledge_ingest.pg_store import upsert_page_links
        await upsert_page_links(
            org_id="org1",
            kb_slug="kb1",
            from_url="https://help.example.com/page",
            links=[{"href": "", "text": "Bad link"}, {"href": None, "text": "Also bad"}],
        )

    mock_pool.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_page_links_link_text_truncated() -> None:
    """link_text is truncated to 500 characters."""
    mock_pool = _make_mock_pool()
    long_text = "x" * 600

    get_pool_patch = patch(
        "knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=mock_pool
    )
    with get_pool_patch:
        from knowledge_ingest.pg_store import upsert_page_links
        await upsert_page_links(
            org_id="org1",
            kb_slug="kb1",
            from_url="https://help.example.com/page",
            links=[{"href": "/other", "text": long_text}],
        )

    link_text_stored = _first_row(mock_pool)[4]  # index 4 = link_text
    assert len(link_text_stored) == 500


@pytest.mark.asyncio
async def test_page_links_not_saved_in_ingest_crawl_result() -> None:
    """
    _ingest_crawl_result does NOT call upsert_page_links directly.

    SPEC-CRAWLER-005 REQ-01.3: link graph building is Phase 1
    (_build_link_graph), not Phase 2 (_ingest_crawl_result).
    upsert_page_links is called by _build_link_graph BEFORE the per-page
    ingest loop runs, ensuring the full graph is available for all pages.
    """
    from knowledge_ingest.crawl4ai_client import CrawlResult

    internal_links = [
        {"href": "/docs/api", "text": "API docs"},
        {"href": "/docs/guide", "text": "Guide"},
    ]
    result = CrawlResult(
        url="https://help.example.com/page",
        fit_markdown="# Page",
        raw_markdown="# Page",
        html="<html><body>Page</body></html>",
        word_count=2,
        success=True,
        links={"internal": internal_links},
        response_headers={},
        metadata={},
    )

    mock_upsert_links = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchval = AsyncMock(return_value=0)

    with patch("knowledge_ingest.pg_store.upsert_crawled_page",
               new_callable=AsyncMock), \
         patch("knowledge_ingest.pg_store.upsert_page_links", mock_upsert_links), \
         patch("knowledge_ingest.routes.ingest.ingest_document",
               new_callable=AsyncMock, return_value={"status": "ok", "chunks": 1}):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result
        await _ingest_crawl_result(
            result,
            "https://help.example.com/page", "org1", "kb1",
            pool=mock_pool,
            stored=None,
        )

    # Phase 2 (_ingest_crawl_result) must NOT call upsert_page_links.
    # That is Phase 1 (_build_link_graph)'s responsibility.
    mock_upsert_links.assert_not_called()
