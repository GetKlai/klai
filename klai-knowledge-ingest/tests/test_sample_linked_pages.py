"""``sample_linked_pages`` — answer "is this SITE worth crawling?" in one request.

A navigation hub is thin by nature, so the seed page alone cannot decide
whether a site holds content. Sampling the pages behind it can — but only if
it is cheap: routing through ``crawl_site`` re-fetched the seed and probed
sitemaps first, three sequential network phases that blew the preview's
wall-clock budget on support.ascendcloud.com (2026-08-06) and left the
verdict at the very thin-content classification the sample exists to fix.

These tests pin the contract: reuse the seed's links, issue exactly ONE bulk
request, and never raise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest.crawl4ai_client import (
    CrawlResult,
    _sample_candidates,
    sample_linked_pages,
)


def _seed(*hrefs: str, url: str = "https://example.com/app/main") -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown="hub",
        raw_markdown="hub",
        html="<html></html>",
        word_count=92,
        success=True,
        links={"internal": [{"href": h} for h in hrefs]},
    )


def _page(url: str, words: int) -> dict:
    md = "Real article prose about the product. " * max(1, words // 6)
    return {
        "url": url,
        "markdown": {"fit_markdown": md, "raw_markdown": md},
        "html": "<html></html>",
        "success": True,
    }


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def test_candidates_prefer_deep_pages_over_sibling_hubs() -> None:
    """Leaf articles answer the question; more hubs do not."""
    seed = _seed(
        "https://example.com/support",
        "https://example.com/app/articles/detail/a_id/16781",
        "https://example.com/app/articles/detail/a_id/15937",
    )
    candidates = _sample_candidates(seed, base_domain="example.com", limit=2)
    assert all("/articles/detail/" in c for c in candidates), candidates


def test_candidates_skip_the_seed_itself_and_duplicates() -> None:
    seed = _seed(
        "https://example.com/app/main",  # the seed
        "https://example.com/app/main/",  # same after canonicalisation
        "https://example.com/a/b/c",
    )
    assert _sample_candidates(seed, base_domain="example.com", limit=5) == [
        "https://example.com/a/b/c"
    ]


def test_candidates_skip_other_domains() -> None:
    seed = _seed("https://elsewhere.test/a/b", "https://example.com/a/b")
    candidates = _sample_candidates(seed, base_domain="example.com", limit=5)
    assert candidates == ["https://example.com/a/b"]


def test_candidates_respect_the_limit() -> None:
    seed = _seed(*[f"https://example.com/a/b/{i}" for i in range(20)])
    assert len(_sample_candidates(seed, base_domain="example.com", limit=5)) == 5


def test_candidates_drop_unrendered_template_urls() -> None:
    """A client-rendered site whose framework never boots leaves ``{{...}}``
    tokens in its hrefs. Those are unfetchable — sampling them would burn the
    whole time budget for nothing (support.ascendcloud.com, 2026-08-12)."""
    seed = _seed(
        "https://example.com/euf/themes/standard/{{item.URL}}",
        "https://example.com/euf/themes/standard/{{item.SeoTitle}}",
        "https://example.com/app/articles/detail/a_id/16781",
    )
    candidates = _sample_candidates(seed, base_domain="example.com", limit=5)
    assert candidates == ["https://example.com/app/articles/detail/a_id/16781"]


def test_all_template_urls_yield_no_candidates() -> None:
    """The full ascendcloud base-URL shape: every link is a template token, so
    there is nothing to sample and the caller must not issue a bulk request."""
    seed = _seed(
        "https://example.com/euf/themes/standard/{{item.URL}}",
        "https://example.com/euf/themes/standard/{{item.SeoTitle}}",
        "https://example.com/euf/themes/standard/{{selectedLanguage.text}}",
    )
    assert _sample_candidates(seed, base_domain="example.com", limit=5) == []


def test_percent_encoded_braces_are_kept() -> None:
    """A correctly percent-encoded URL is valid and must NOT be filtered — the
    guard rejects literal ``{``/``}`` (unrendered tokens), not encoded ones."""
    seed = _seed("https://example.com/a/b?state=%7B%22x%22%3A1%7D")
    assert _sample_candidates(seed, base_domain="example.com", limit=5) == [
        "https://example.com/a/b?state=%7B%22x%22%3A1%7D"
    ]


# ---------------------------------------------------------------------------
# Sampling behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_counts_only_pages_with_real_content() -> None:
    seed = _seed(
        "https://example.com/a/b/1",
        "https://example.com/a/b/2",
        "https://example.com/a/b/3",
    )
    raw = [
        _page("https://example.com/a/b/1", 900),
        _page("https://example.com/a/b/2", 5),  # thin — must not count
        _page("https://example.com/a/b/3", 1200),
    ]
    with patch(
        "knowledge_ingest.crawl4ai_client._chunked_bulk_fetch",
        new=AsyncMock(return_value=(raw, None)),
    ):
        sample = await sample_linked_pages(seed, max_pages=3)
    assert sample.pages_crawled == 3
    assert sample.pages_usable == 2


@pytest.mark.asyncio
async def test_sample_issues_exactly_one_bulk_request() -> None:
    """The whole point: one parallel batch, no seed re-fetch, no sitemap probe."""
    seed = _seed(*[f"https://example.com/a/b/{i}" for i in range(5)])
    bulk = AsyncMock(
        return_value=([_page(f"https://example.com/a/b/{i}", 900) for i in range(5)], None)
    )
    with patch("knowledge_ingest.crawl4ai_client._chunked_bulk_fetch", new=bulk):
        await sample_linked_pages(seed, max_pages=5)
    assert bulk.await_count == 1
    assert len(bulk.await_args.kwargs["urls"]) == 5


@pytest.mark.asyncio
async def test_seed_without_links_costs_no_request() -> None:
    bulk = AsyncMock(return_value=([], None))
    with patch("knowledge_ingest.crawl4ai_client._chunked_bulk_fetch", new=bulk):
        sample = await sample_linked_pages(_seed(), max_pages=5)
    assert (sample.pages_crawled, sample.pages_usable) == (0, 0)
    bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_with_only_template_links_costs_no_request() -> None:
    """The ascendcloud case end-to-end: all links are unrendered tokens, so no
    bulk request fires and the sample returns instantly (no wasted budget)."""
    seed = _seed(
        "https://example.com/euf/themes/standard/{{item.URL}}",
        "https://example.com/euf/themes/standard/{{item.SeoTitle}}",
    )
    bulk = AsyncMock(return_value=([], None))
    with patch("knowledge_ingest.crawl4ai_client._chunked_bulk_fetch", new=bulk):
        sample = await sample_linked_pages(seed, max_pages=5)
    assert (sample.pages_crawled, sample.pages_usable) == (0, 0)
    bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_seed_yields_empty_sample() -> None:
    dead = CrawlResult(
        url="https://example.com/app/main",
        fit_markdown="",
        raw_markdown="",
        html="",
        word_count=0,
        success=False,
    )
    bulk = AsyncMock(return_value=([], None))
    with patch("knowledge_ingest.crawl4ai_client._chunked_bulk_fetch", new=bulk):
        sample = await sample_linked_pages(dead, max_pages=5)
    assert (sample.pages_crawled, sample.pages_usable) == (0, 0)
    bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_error_degrades_to_zero_sample() -> None:
    """Callers fall back to their single-page verdict; the preview must not error."""
    seed = _seed("https://example.com/a/b/1")
    with patch(
        "knowledge_ingest.crawl4ai_client._chunked_bulk_fetch",
        new=AsyncMock(return_value=([], RuntimeError("crawl4ai down"))),
    ):
        sample = await sample_linked_pages(seed, max_pages=5)
    assert (sample.pages_crawled, sample.pages_usable) == (0, 0)
