"""
Tests for pg_store.upsert_page_links.

Covers:
- Relative URL resolution via urljoin
- link_text truncated at 500 characters
- Empty href entries are skipped
- Absolute URLs stored as-is

SPEC-TI-003-FOLLOWUP-001: pg_store helpers take asyncpg.Connection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _first_row(mock_conn: MagicMock) -> tuple:
    """Return the first row tuple from the executemany call."""
    rows = mock_conn.executemany.call_args.args[1]
    return rows[0]


@pytest.mark.asyncio
async def test_page_links_relative_url_resolution() -> None:
    """Relative hrefs are resolved against from_url before storing."""
    mock_conn = _make_mock_conn()

    from knowledge_ingest.pg_store import upsert_page_links

    await upsert_page_links(
        mock_conn,
        org_id="org1",
        kb_slug="kb1",
        from_url="https://help.example.com/docs/guide",
        links=[{"href": "../api/overview", "text": "API Overview"}],
    )

    mock_conn.executemany.assert_called_once()
    to_url = _first_row(mock_conn)[3]  # index 3 = to_url
    assert to_url in (
        "https://help.example.com/docs/../api/overview",
        "https://help.example.com/api/overview",
    ), f"Unexpected to_url: {to_url}"


@pytest.mark.asyncio
async def test_page_links_absolute_url_stored_as_is() -> None:
    """Absolute hrefs are stored without modification."""
    mock_conn = _make_mock_conn()

    from knowledge_ingest.pg_store import upsert_page_links

    await upsert_page_links(
        mock_conn,
        org_id="org1",
        kb_slug="kb1",
        from_url="https://help.example.com/docs/guide",
        links=[{"href": "https://other.example.com/page", "text": "External"}],
    )

    to_url = _first_row(mock_conn)[3]
    assert to_url == "https://other.example.com/page"


@pytest.mark.asyncio
async def test_page_links_empty_href_skipped() -> None:
    """Links with empty href are silently skipped."""
    mock_conn = _make_mock_conn()

    from knowledge_ingest.pg_store import upsert_page_links

    await upsert_page_links(
        mock_conn,
        org_id="org1",
        kb_slug="kb1",
        from_url="https://help.example.com/page",
        links=[{"href": "", "text": "Bad link"}, {"href": None, "text": "Also bad"}],
    )

    mock_conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_page_links_link_text_truncated() -> None:
    """link_text is truncated to 500 characters."""
    mock_conn = _make_mock_conn()
    long_text = "x" * 600

    from knowledge_ingest.pg_store import upsert_page_links

    await upsert_page_links(
        mock_conn,
        org_id="org1",
        kb_slug="kb1",
        from_url="https://help.example.com/page",
        links=[{"href": "/other", "text": long_text}],
    )

    link_text_stored = _first_row(mock_conn)[4]  # index 4 = link_text
    assert len(link_text_stored) == 500


@pytest.mark.asyncio
async def test_page_links_not_saved_in_ingest_crawl_result() -> None:
    """
    _ingest_crawl_result does NOT call upsert_page_links directly.

    SPEC-CRAWLER-005 REQ-01.3: link graph building is Phase 1
    (_build_link_graph), not Phase 2 (_ingest_crawl_result).
    upsert_page_links is called by _build_link_graph BEFORE the per-page
    ingest loop runs, ensuring the full graph is available for all pages.

    SPEC-TI-003-FOLLOWUP-001: _ingest_crawl_result now takes conn as
    first arg.
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
    mock_conn = _make_mock_conn()

    # _ingest_crawl_result calls _build_image_store internally; stub it out.
    @asynccontextmanager
    async def _no_image_store():
        yield None

    with (
        patch("knowledge_ingest.pg_store.upsert_crawled_page", new_callable=AsyncMock),
        patch("knowledge_ingest.pg_store.upsert_page_links", mock_upsert_links),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
            return_value={"status": "ok", "chunks": 1},
        ),
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            "https://help.example.com/page",
            "org1",
            "kb1",
            stored=None,
        )

    # Phase 2 (_ingest_crawl_result) must NOT call upsert_page_links.
    # That is Phase 1 (_build_link_graph)'s responsibility.
    mock_upsert_links.assert_not_called()
