"""
Tests for link graph field population in crawl_url() (SPEC-CRAWLER-003 TASK-005,
restored under SPEC-CRAWLER-005 Fase 2).

Verifies that `POST /ingest/v1/crawl` populates `ingest_req.extra` with
`links_to`, `anchor_texts`, and `incoming_link_count` when link_graph data
is available.

Diagnosis (SPEC-CRAWLER-005 Fase 2):
  The original tests patched `knowledge_ingest.routes.crawl.httpx.AsyncClient`.
  `routes/crawl.py` was refactored to use `crawl4ai_client._run_crawl` /
  `crawl_page` instead of direct httpx calls, so the patch target no longer
  exists on that module. The ingest-side contract these tests protect
  (link_graph fields in extra) is unchanged — only the mock wiring needs
  to match the current implementation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.models import CrawlRequest


def _make_mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_run_crawl_result(
    fit_markdown: str = "Hello world\n\nSome prose here.",
    html: str = "<html><body><p>Hello world</p></body></html>",
) -> tuple[str, int, str]:
    """Return the (fit_markdown, word_count, raw_html) tuple that
    routes/crawl.py::_run_crawl produces."""
    word_count = len(fit_markdown.split())
    return fit_markdown, word_count, html


@pytest.mark.asyncio
async def test_crawl_url_populates_link_fields():
    """When link_graph returns data, extra contains links_to, anchor_texts,
    incoming_link_count."""
    mock_pool = _make_mock_pool()

    with patch("knowledge_ingest.routes.crawl._run_crawl", new_callable=AsyncMock,
               return_value=_make_run_crawl_result()), \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.get_domain_selector",
               new_callable=AsyncMock, return_value=None), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock,
                      return_value=["https://example.com/b", "https://example.com/c"]), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock,
                      return_value=["Page B", "Page C"]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock,
                      return_value=5), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock,
               return_value=mock_pool):

        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()

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

    with patch("knowledge_ingest.routes.crawl._run_crawl", new_callable=AsyncMock,
               return_value=_make_run_crawl_result()), \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.get_domain_selector",
               new_callable=AsyncMock, return_value=None), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock,
                      return_value=[f"https://example.com/page-{i}" for i in range(25)]), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock,
               return_value=mock_pool):

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
    """When link_graph.get_outbound_urls raises, crawl still succeeds with
    source_url only (no link fields in extra)."""
    mock_pool = _make_mock_pool()

    with patch("knowledge_ingest.routes.crawl._run_crawl", new_callable=AsyncMock,
               return_value=_make_run_crawl_result()), \
         patch("knowledge_ingest.routes.crawl.pg_store") as mock_pg, \
         patch("knowledge_ingest.routes.crawl.ingest_document",
               new_callable=AsyncMock) as mock_ingest, \
         patch("knowledge_ingest.routes.crawl.validate_url", new_callable=AsyncMock), \
         patch("knowledge_ingest.routes.crawl.get_domain_selector",
               new_callable=AsyncMock, return_value=None), \
         patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock,
                      side_effect=Exception("DB connection failed")), \
         patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]), \
         patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0), \
         patch("knowledge_ingest.routes.crawl.get_pool", new_callable=AsyncMock,
               return_value=mock_pool):

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
