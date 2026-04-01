"""
Tests for crawl-registry deduplication.

Covers _crawl_and_ingest_page (bulk crawler) and crawl_url (single-URL route):
- Unchanged content → ingest_document NOT called
- Changed content   → ingest_document IS called and crawled_pages updated
- New URL           → insert + ingest
- skip path returns chunks_ingested=0
- crawled_pages keyed on request.url, not derived path
"""
from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_crawl_result(
    text: str = "# Hello\nPage content here.",
    links: dict | None = None,
    success: bool = True,
) -> MagicMock:
    """Build a minimal crawl4ai-style result object."""
    result = MagicMock()
    result.success = success
    result.error_message = ""
    result.markdown.fit_markdown = text
    result.markdown.raw_markdown = text
    result.response_headers = {}
    result.metadata = {}
    result.links = links if links is not None else {"internal": []}
    return result


# ---------------------------------------------------------------------------
# Bulk crawler: _crawl_and_ingest_page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_crawl_skip_unchanged() -> None:
    """When stored_hash == new hash, ingest_document is NOT called."""
    text = "# Hello\nPage content here."
    stored = _sha256(text)

    mock_crawler = MagicMock()
    mock_crawler.arun = AsyncMock(return_value=_make_crawl_result(text=text))

    with patch(
        "knowledge_ingest.pg_store.upsert_crawled_page",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
    ) as mock_ingest:
        from knowledge_ingest.adapters.crawler import _crawl_and_ingest_page
        await _crawl_and_ingest_page(
            mock_crawler, MagicMock(), "https://example.com/page", "org1", "kb1", 0.0,
            stored_hash=stored,
        )

    mock_ingest.assert_not_called()
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_crawl_reingest_on_change() -> None:
    """When hash differs, ingest_document IS called and crawled_pages is updated."""
    text = "# Updated content"
    old_hash = _sha256("# Old content")

    mock_crawler = MagicMock()
    mock_crawler.arun = AsyncMock(return_value=_make_crawl_result(text=text))

    with patch(
        "knowledge_ingest.pg_store.upsert_crawled_page",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "knowledge_ingest.pg_store.upsert_page_links",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
        return_value={"status": "ok", "chunks": 3},
    ) as mock_ingest:
        from knowledge_ingest.adapters.crawler import _crawl_and_ingest_page
        await _crawl_and_ingest_page(
            mock_crawler, MagicMock(), "https://example.com/page", "org1", "kb1", 0.0,
            stored_hash=old_hash,
        )

    mock_ingest.assert_called_once()
    mock_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_crawl_new_page() -> None:
    """When URL is not in crawled_pages (None), insert + ingest."""
    text = "# Brand new page"

    mock_crawler = MagicMock()
    mock_crawler.arun = AsyncMock(return_value=_make_crawl_result(text=text))

    with patch(
        "knowledge_ingest.pg_store.upsert_crawled_page",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "knowledge_ingest.pg_store.upsert_page_links",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
        return_value={"status": "ok", "chunks": 2},
    ) as mock_ingest:
        from knowledge_ingest.adapters.crawler import _crawl_and_ingest_page
        await _crawl_and_ingest_page(
            mock_crawler, MagicMock(), "https://example.com/new", "org1", "kb1", 0.0,
            stored_hash=None,
        )

    mock_ingest.assert_called_once()
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["url"] == "https://example.com/new"
    assert call_kwargs["content_hash"] == _sha256(text)


# ---------------------------------------------------------------------------
# Single-URL crawl: crawl_url
# ---------------------------------------------------------------------------

def _patch_crawl_url_deps(stored_hash: Any, ingest_return: dict) -> list:
    """Return a list of patch context managers for crawl_url tests."""
    return [
        patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock),
        patch("knowledge_ingest.routes.crawl.pg_store.get_crawled_page_hash",
              new_callable=AsyncMock, return_value=stored_hash),
        patch("knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page",
              new_callable=AsyncMock),
        patch("knowledge_ingest.routes.crawl.ingest_document",
              new_callable=AsyncMock, return_value=ingest_return),
    ]


@pytest.mark.asyncio
async def test_single_url_skip_unchanged() -> None:
    """crawl_url returns chunks_ingested=0 when hash matches stored hash."""
    html = "<html><body><h1>Hello</h1></body></html>"
    import html2text as h2t
    conv = h2t.HTML2Text()
    conv.ignore_links = False
    conv.ignore_images = True
    conv.body_width = 0
    markdown = conv.handle(html)
    stored = _sha256(markdown)

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.pg_store.get_crawled_page_hash",
               new_callable=AsyncMock, return_value=stored), \
         patch("knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page",
               new_callable=AsyncMock) as mock_upsert, \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock) as mock_ingest, \
         patch("httpx.AsyncClient", return_value=mock_client):
        from knowledge_ingest.models import CrawlRequest
        from knowledge_ingest.routes.crawl import crawl_url
        result = await crawl_url(CrawlRequest(
            org_id="org1", kb_slug="kb1", url="https://example.com/page"
        ))

    assert result.chunks_ingested == 0
    mock_ingest.assert_not_called()
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_single_url_url_key() -> None:
    """crawled_pages is keyed on request.url, not the derived path."""
    html = "<html><body><p>Content</p></body></html>"
    url = "https://example.com/docs/api/overview"

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_upsert = AsyncMock()

    with patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.pg_store.get_crawled_page_hash",
               new_callable=AsyncMock, return_value=None), \
         patch("knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page", mock_upsert), \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock, return_value={"status": "ok", "chunks": 1}), \
         patch("httpx.AsyncClient", return_value=mock_client):
        from knowledge_ingest.models import CrawlRequest
        from knowledge_ingest.routes.crawl import crawl_url
        await crawl_url(CrawlRequest(org_id="org1", kb_slug="kb1", url=url))

    mock_upsert.assert_called_once()
    assert mock_upsert.call_args.kwargs["url"] == url


# ---------------------------------------------------------------------------
# KB cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_kb_cleans_registry() -> None:
    """delete_kb removes rows from crawled_pages and page_links."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    get_pool_patch = patch(
        "knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=mock_pool
    )
    with get_pool_patch:
        from knowledge_ingest.pg_store import delete_kb
        await delete_kb("org1", "kb1")

    executed_sqls = [call.args[0] for call in mock_conn.execute.call_args_list]
    assert any("crawled_pages" in sql for sql in executed_sqls), \
        "crawled_pages not deleted"
    assert any("page_links" in sql for sql in executed_sqls), \
        "page_links not deleted"
