"""
Tests for crawl-registry deduplication (dual-hash strategy).

Covers _ingest_crawl_result (bulk crawler) and crawl_url (single-URL route):
- raw HTML unchanged        → skip everything, ingest_document NOT called
- HTML noise, content same  → update raw_html_hash, skip ingest
- Content changed           → ingest_document IS called and crawled_pages updated
- New URL                   → insert + ingest
- crawled_pages keyed on request.url, not derived path
"""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.crawl4ai_client import CrawlResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_crawl_result(
    text: str = "# Hello\nPage content here.",
    html: str = "<html><body>Hello</body></html>",
    links: dict | None = None,
    success: bool = True,
) -> CrawlResult:
    """Build a CrawlResult for testing."""
    return CrawlResult(
        url="https://example.com/page",
        fit_markdown=text,
        raw_markdown=text,
        html=html,
        word_count=len(text.split()),
        success=success,
        links=links if links is not None else {"internal": []},
        response_headers={},
        metadata={},
    )


# ---------------------------------------------------------------------------
# Bulk crawler: _ingest_crawl_result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_crawl_skip_unchanged() -> None:
    """When raw_html_hash matches stored, ingest_document is NOT called."""
    html = "<html><body>Hello</body></html>"
    text = "# Hello\nPage content here."
    stored = (_sha256(html), _sha256(text))  # (raw_html_hash, content_hash)

    result = _make_crawl_result(text=text, html=html)

    with patch(
        "knowledge_ingest.pg_store.upsert_crawled_page",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
    ) as mock_ingest:
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result
        await _ingest_crawl_result(
            result, "https://example.com/page", "org1", "kb1",
            stored=stored,
        )

    mock_ingest.assert_not_called()
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_crawl_skip_html_noise() -> None:
    """When raw HTML changed but content hash is identical, skip ingest but update raw_html_hash."""
    text = "# Article content"
    old_html = "<html><body>Article</body></html>"
    new_html = "<html><body>Article<script>analytics()</script></body></html>"
    stored = (_sha256(old_html), _sha256(text))

    result = _make_crawl_result(text=text, html=new_html)

    with patch(
        "knowledge_ingest.pg_store.upsert_crawled_page",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
    ) as mock_ingest:
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result
        await _ingest_crawl_result(
            result, "https://example.com/page", "org1", "kb1",
            stored=stored,
        )

    mock_ingest.assert_not_called()
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args.kwargs["raw_html_hash"] == _sha256(new_html)


@pytest.mark.asyncio
async def test_bulk_crawl_reingest_on_change() -> None:
    """When content hash differs, ingest_document IS called and crawled_pages is updated."""
    text = "# Updated content"
    html = "<html><body>Updated</body></html>"
    old_stored = (_sha256("old html"), _sha256("# Old content"))

    result = _make_crawl_result(text=text, html=html)

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
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result
        await _ingest_crawl_result(
            result, "https://example.com/page", "org1", "kb1",
            stored=old_stored,
        )

    mock_ingest.assert_called_once()
    mock_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_crawl_new_page() -> None:
    """When URL is not in crawled_pages (None), insert + ingest."""
    text = "# Brand new page"
    html = "<html><body>New</body></html>"

    result = _make_crawl_result(text=text, html=html)

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
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result
        await _ingest_crawl_result(
            result, "https://example.com/new", "org1", "kb1",
            stored=None,
        )

    mock_ingest.assert_called_once()
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args.kwargs
    assert call_kwargs["url"] == "https://example.com/new"
    assert call_kwargs["raw_html_hash"] == _sha256(html)
    assert call_kwargs["content_hash"] == _sha256(text)


# ---------------------------------------------------------------------------
# Single-URL crawl: crawl_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_url_skip_unchanged() -> None:
    """crawl_url returns chunks_ingested=0 when raw HTML hash matches stored."""
    raw_html = "<html><body><h1>Hello</h1></body></html>"
    fit_md = "# Hello"
    stored = (_sha256(raw_html), "some-content-hash")

    with patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.get_domain_selector",
               new_callable=AsyncMock, return_value=None), \
         patch("knowledge_ingest.routes.crawl._run_crawl",
               new_callable=AsyncMock, return_value=(fit_md, 2, raw_html)), \
         patch("knowledge_ingest.routes.crawl.pg_store.get_crawled_page_stored",
               new_callable=AsyncMock, return_value=stored), \
         patch("knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page",
               new_callable=AsyncMock) as mock_upsert, \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock) as mock_ingest:
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
    url = "https://example.com/docs/api/overview"
    raw_html = "<html><body><p>Content</p></body></html>"
    fit_md = "Content"
    mock_upsert = AsyncMock()

    with patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.get_domain_selector",
               new_callable=AsyncMock, return_value=None), \
         patch("knowledge_ingest.routes.crawl._run_crawl",
               new_callable=AsyncMock, return_value=(fit_md, 1, raw_html)), \
         patch("knowledge_ingest.routes.crawl.pg_store.get_crawled_page_stored",
               new_callable=AsyncMock, return_value=None), \
         patch("knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page", mock_upsert), \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock, return_value={"status": "ok", "chunks": 1}):
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
