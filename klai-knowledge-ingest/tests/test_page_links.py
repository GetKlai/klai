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
    pool.execute = AsyncMock(return_value=None)
    return pool


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

    mock_pool.execute.assert_called_once()
    call_args = mock_pool.execute.call_args
    # $4 is to_url (positional index 3 in args after the SQL)
    to_url = call_args.args[4]
    assert to_url == "https://help.example.com/docs/../api/overview" or \
           to_url == "https://help.example.com/api/overview", \
           f"Unexpected to_url: {to_url}"


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

    to_url = mock_pool.execute.call_args.args[4]
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

    mock_pool.execute.assert_not_called()


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

    link_text_stored = mock_pool.execute.call_args.args[5]
    assert len(link_text_stored) == 500


@pytest.mark.asyncio
async def test_page_links_saved_in_bulk_crawl() -> None:
    """_crawl_and_ingest_page calls upsert_page_links with internal links."""
    from unittest.mock import MagicMock

    internal_links = [
        {"href": "/docs/api", "text": "API docs"},
        {"href": "/docs/guide", "text": "Guide"},
    ]
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.error_message = ""
    mock_result.markdown.fit_markdown = "# Page"
    mock_result.markdown.raw_markdown = "# Page"
    mock_result.response_headers = {}
    mock_result.metadata = {}
    mock_result.links = {"internal": internal_links}

    mock_crawler = MagicMock()
    mock_crawler.arun = AsyncMock(return_value=mock_result)

    mock_upsert_links = AsyncMock()

    with patch("knowledge_ingest.pg_store.get_crawled_page_hash",
               new_callable=AsyncMock, return_value=None), \
         patch("knowledge_ingest.pg_store.upsert_crawled_page",
               new_callable=AsyncMock), \
         patch("knowledge_ingest.pg_store.upsert_page_links", mock_upsert_links), \
         patch("knowledge_ingest.routes.ingest.ingest_document",
               new_callable=AsyncMock, return_value={"status": "ok", "chunks": 1}):
        from knowledge_ingest.adapters.crawler import _crawl_and_ingest_page
        await _crawl_and_ingest_page(
            mock_crawler, MagicMock(),
            "https://help.example.com/page", "org1", "kb1", 0.0,
        )

    mock_upsert_links.assert_called_once()
    call_kwargs = mock_upsert_links.call_args.kwargs
    assert call_kwargs["from_url"] == "https://help.example.com/page"
    assert call_kwargs["links"] == internal_links
