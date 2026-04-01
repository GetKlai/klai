"""
Tests for link graph field population in crawl_url() (SPEC-CRAWLER-003, TASK-005).

Verifies that crawl_url populates extra with links_to, anchor_texts,
and incoming_link_count when link_graph data is available.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from knowledge_ingest import link_graph
from knowledge_ingest.models import CrawlRequest


def _make_mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_httpx_response(text: str = "<html><body><p>Hello world</p></body></html>", status_code: int = 200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_crawl_url_populates_link_fields():
    """When link_graph returns data, extra contains links_to, anchor_texts, incoming_link_count."""
    mock_pool = _make_mock_pool()
    mock_resp = _make_httpx_response()

    with patch("knowledge_ingest.routes.crawl.httpx.AsyncClient") as mock_client_cls, \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document", new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=["https://example.com/b", "https://example.com/c"]), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=["Page B", "Page C"]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=5), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock, return_value=mock_pool):

        # httpx mock
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # pg_store mock
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()

        # ingest mock
        mock_ingest.return_value = {"chunks": 3}

        from knowledge_ingest.routes.crawl import crawl_url

        request = CrawlRequest(
            url="https://example.com/a",
            org_id="org-1",
            kb_slug="docs",
        )
        result = await crawl_url(request)

        # Verify ingest was called with link fields in extra
        mock_ingest.assert_called_once()
        ingest_req = mock_ingest.call_args[0][0]
        assert ingest_req.extra["source_url"] == "https://example.com/a"
        assert ingest_req.extra["links_to"] == ["https://example.com/b", "https://example.com/c"]
        assert ingest_req.extra["anchor_texts"] == ["Page B", "Page C"]
        assert ingest_req.extra["incoming_link_count"] == 5
        assert result.chunks_ingested == 3


@pytest.mark.asyncio
async def test_crawl_url_caps_links_to_at_20():
    """When link_graph returns >20 outbound URLs, links_to is capped at 20."""
    mock_pool = _make_mock_pool()
    mock_resp = _make_httpx_response()

    with patch("knowledge_ingest.routes.crawl.httpx.AsyncClient") as mock_client_cls, \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document", new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[f"https://example.com/page-{i}" for i in range(25)]), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock, return_value=mock_pool):

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()

        mock_ingest.return_value = {"chunks": 1}

        from knowledge_ingest.routes.crawl import crawl_url

        request = CrawlRequest(
            url="https://example.com/hub",
            org_id="org-1",
            kb_slug="docs",
        )
        await crawl_url(request)

        ingest_req = mock_ingest.call_args[0][0]
        assert len(ingest_req.extra["links_to"]) == 20


@pytest.mark.asyncio
async def test_crawl_url_graceful_degradation_on_link_graph_error():
    """When link_graph raises, crawl still succeeds with source_url only."""
    mock_pool = _make_mock_pool()
    mock_resp = _make_httpx_response()

    with patch("knowledge_ingest.routes.crawl.httpx.AsyncClient") as mock_client_cls, \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document", new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, side_effect=Exception("DB connection failed")), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock, return_value=mock_pool):

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()

        mock_ingest.return_value = {"chunks": 2}

        from knowledge_ingest.routes.crawl import crawl_url

        request = CrawlRequest(
            url="https://example.com/a",
            org_id="org-1",
            kb_slug="docs",
        )
        result = await crawl_url(request)

        # Crawl should still succeed
        assert result.chunks_ingested == 2

        ingest_req = mock_ingest.call_args[0][0]
        assert ingest_req.extra["source_url"] == "https://example.com/a"
        # Link fields should NOT be present since the gather failed
        assert "links_to" not in ingest_req.extra
        assert "anchor_texts" not in ingest_req.extra
        assert "incoming_link_count" not in ingest_req.extra
