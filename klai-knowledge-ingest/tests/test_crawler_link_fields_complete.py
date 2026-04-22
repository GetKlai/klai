"""
End-to-end tests for the two-phase crawl pipeline (SPEC-CRAWLER-005 Fase 1).

Verifies:
1. run_crawl_job builds the full link graph BEFORE per-page ingest.
2. Every ingested page gets correct link fields because the graph is ready.
3. qdrant_store.update_link_counts is NEVER called (REQ-05.1).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import link_graph
from knowledge_ingest.crawl4ai_client import CrawlResult


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _make_crawl_result(
    url: str,
    internal_links: list[dict] | None = None,
) -> CrawlResult:
    """Build a successful CrawlResult with the given internal links."""
    text = f"Markdown content for {url}"
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html=f"<html><body><p>Content for {url}</p></body></html>",
        word_count=4,
        success=True,
        links={"internal": internal_links or []},
        error_message=None,
        metadata={},
        response_headers={"content-type": "text/html"},
    )


def _build_cross_linked_results() -> list[CrawlResult]:
    """
    5 cross-linked pages forming a cycle: A→B, A→C, B→C, C→D, D→A.

    Expected incoming counts (how many pages link TO each URL):
    - A: 1 (D→A)
    - B: 1 (A→B)
    - C: 2 (A→C, B→C)
    - D: 1 (C→D)
    - E: 0 (no pages link to E)
    """
    base = "https://example.com"
    a, b, c, d, e = (
        f"{base}/a",
        f"{base}/b",
        f"{base}/c",
        f"{base}/d",
        f"{base}/e",
    )
    return [
        _make_crawl_result(a, [{"href": b, "text": "Link to B"}, {"href": c, "text": "Link to C"}]),
        _make_crawl_result(b, [{"href": c, "text": "Link to C"}]),
        _make_crawl_result(c, [{"href": d, "text": "Link to D"}]),
        _make_crawl_result(d, [{"href": a, "text": "Link to A"}]),
        _make_crawl_result(e, []),  # E has no outbound links
    ]


def _build_in_memory_graph(results: list[CrawlResult]) -> dict[str, list[dict]]:
    """
    Build an in-memory link graph from CrawlResult fixtures.
    Returns {from_url: [{"href": to_url, "text": anchor_text}]}.
    """
    graph: dict[str, list[dict]] = {}
    for result in results:
        internal = (result.links or {}).get("internal") or []
        if internal:
            graph[result.url] = internal
    return graph


def _get_outbound_urls_from_graph(
    graph: dict[str, list[dict]],
) -> AsyncMock:
    """Return an AsyncMock for link_graph.get_outbound_urls backed by in-memory graph."""
    async def _impl(url: str, org_id: str, kb_slug: str, pool: object) -> list[str]:
        return [link["href"] for link in graph.get(url, [])]
    return AsyncMock(side_effect=_impl)


def _get_anchor_texts_from_graph(
    graph: dict[str, list[dict]],
) -> AsyncMock:
    """Return an AsyncMock for link_graph.get_anchor_texts backed by in-memory graph."""
    async def _impl(url: str, org_id: str, kb_slug: str, pool: object) -> list[str]:
        texts = []
        for _from_url, links in graph.items():
            for link in links:
                if link["href"] == url and link.get("text"):
                    texts.append(link["text"])
        return texts
    return AsyncMock(side_effect=_impl)


def _get_incoming_count_from_graph(
    graph: dict[str, list[dict]],
) -> AsyncMock:
    """Return an AsyncMock for link_graph.get_incoming_count backed by in-memory graph."""
    async def _impl(url: str, org_id: str, kb_slug: str, pool: object) -> int:
        count = 0
        for _from_url, links in graph.items():
            for link in links:
                if link["href"] == url:
                    count += 1
        return count
    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_run_crawl_job_populates_link_fields_on_every_page():
    """
    Two-phase pipeline test: link graph is built BEFORE first ingest, so all
    pages receive correct incoming_link_count, anchor_texts, and links_to.

    Link topology: A→B, A→C, B→C, C→D, D→A (plus isolated E).
    Expected incoming counts: A=1, B=1, C=2, D=1, E=0.
    """
    results = _build_cross_linked_results()
    graph = _build_in_memory_graph(results)
    mock_pool = _make_mock_pool()

    # Capture all ingest calls to verify extra dicts
    captured_ingest_calls: list[dict] = []

    async def _fake_ingest(req):  # type: ignore[no-untyped-def]
        captured_ingest_calls.append({"url": req.path, "extra": dict(req.extra)})
        return {"chunks": 1}

    with (
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
            return_value=results,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch(
            "knowledge_ingest.adapters.crawler._update_job",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            side_effect=_fake_ingest,
        ),
        patch.object(link_graph, "get_outbound_urls", new=_get_outbound_urls_from_graph(graph)),
        patch.object(link_graph, "get_anchor_texts", new=_get_anchor_texts_from_graph(graph)),
        patch.object(link_graph, "get_incoming_count", new=_get_incoming_count_from_graph(graph)),
    ):
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()
        mock_pg.upsert_page_links = AsyncMock()

        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            job_id="job-test",
            org_id="org-1",
            kb_slug="docs",
            start_url="https://example.com/a",
            max_depth=2,
        )

    # upsert_page_links must have been called (Phase 1)
    assert mock_pg.upsert_page_links.call_count >= 1, (
        "Expected _build_link_graph to call upsert_page_links, but it was not called. "
        "This means the two-phase pipeline was NOT applied."
    )

    # All 5 pages must have been ingested
    assert len(captured_ingest_calls) == 5, (
        f"Expected 5 ingest calls, got {len(captured_ingest_calls)}"
    )

    # Build expected counts from graph
    base = "https://example.com"
    expected_incoming = {
        f"{base}/a": 1,   # D→A
        f"{base}/b": 1,   # A→B
        f"{base}/c": 2,   # A→C + B→C
        f"{base}/d": 1,   # C→D
        f"{base}/e": 0,   # nobody links to E
    }

    for entry in captured_ingest_calls:
        url = entry["url"]
        extra = entry["extra"]
        expected_count = expected_incoming[url]

        assert "incoming_link_count" in extra, (
            f"Page {url}: missing 'incoming_link_count' in extra"
        )
        assert extra["incoming_link_count"] == expected_count, (
            f"Page {url}: expected incoming_link_count={expected_count}, "
            f"got {extra['incoming_link_count']}"
        )
        assert "links_to" in extra, f"Page {url}: missing 'links_to' in extra"
        # links_to is capped at 20
        assert len(extra["links_to"]) <= 20

    # Page A is linked from D with anchor "Link to A"
    a_entry = next(e for e in captured_ingest_calls if e["url"] == f"{base}/a")
    assert "anchor_texts" in a_entry["extra"]
    assert "Link to A" in a_entry["extra"]["anchor_texts"], (
        f"Page A should have 'Link to A' in anchor_texts (D→A), "
        f"got: {a_entry['extra'].get('anchor_texts')}"
    )


@pytest.mark.asyncio
async def test_run_crawl_job_no_post_crawl_link_counts_call():
    """
    run_crawl_job must NOT call qdrant_store.update_link_counts (REQ-05.1).

    The two-phase pipeline makes incoming_link_count correct at first write.
    The post-crawl batch update is dead code and must be removed.
    """
    results = [
        _make_crawl_result(
            "https://example.com/a",
            [{"href": "https://example.com/b", "text": "B"}],
        ),
    ]
    mock_pool = _make_mock_pool()

    with (
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
            return_value=results,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch(
            "knowledge_ingest.adapters.crawler._update_job",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler.pg_store") as mock_pg,
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
            return_value={"chunks": 1},
        ),
        patch.object(link_graph, "get_outbound_urls", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_anchor_texts", new_callable=AsyncMock, return_value=[]),
        patch.object(link_graph, "get_incoming_count", new_callable=AsyncMock, return_value=0),
    ):
        mock_pg.get_crawled_page_hashes = AsyncMock(return_value={})
        mock_pg.get_crawled_page_stored = AsyncMock(return_value=None)
        mock_pg.upsert_crawled_page = AsyncMock()
        mock_pg.upsert_page_links = AsyncMock()

        import knowledge_ingest.qdrant_store as qs_mod

        with patch.object(
            qs_mod, "update_link_counts", new_callable=AsyncMock
        ) as mock_update_link_counts:
            from knowledge_ingest.adapters.crawler import run_crawl_job

            await run_crawl_job(
                job_id="job-test",
                org_id="org-1",
                kb_slug="docs",
                start_url="https://example.com/a",
                max_depth=1,
            )

            mock_update_link_counts.assert_not_called(), (
                "qdrant_store.update_link_counts was called from run_crawl_job. "
                "REQ-05.1: this function must NOT be called — the two-phase pipeline "
                "makes the post-crawl batch update dead code."
            )
