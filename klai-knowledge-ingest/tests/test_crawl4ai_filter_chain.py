"""SPEC-INGEST-RECONCILE-001 Fix 1 — include_patterns filtering on candidates.

The legacy test (pre-Reconcile-001) asserted that ``filter_chain`` was
JSON-wrapped correctly inside crawl4ai's ``deep_crawl_strategy`` payload.
That payload no longer exists: ``crawl_site`` now hands a flat URL list
to ``POST /crawl`` and applies ``include_patterns`` as a candidate-side
substring filter. These tests pin the new behaviour:

- When ``include_patterns`` is set, only matching candidates reach the
  bulk request body.
- When ``include_patterns`` is None, every same-domain candidate from
  sitemap+BFS-union reaches the request body.
- The crawler_config payload no longer carries a ``deep_crawl_strategy``
  key (regression guard against the legacy BFS path being reintroduced
  alongside the bulk path).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import CrawlResult


def _seed_result(url: str, internal_hrefs: list[str]) -> CrawlResult:
    """Synthesise the start_url's CrawlResult with internal links."""
    return CrawlResult(
        url=url,
        fit_markdown="Seed page",
        raw_markdown="Seed page",
        html="<html></html>",
        word_count=2,
        success=True,
        links={"internal": [{"href": h, "text": ""} for h in internal_hrefs]},
    )


@pytest.mark.asyncio
async def test_crawl_site_applies_include_patterns_to_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_patterns acts as a substring filter on the candidate URL set."""
    sitemap_urls = [
        "https://wiki.redcactus.cloud/nl/getting-started",
        "https://wiki.redcactus.cloud/en/getting-started",
        "https://wiki.redcactus.cloud/nl/advanced",
    ]
    bfs_links = [
        "https://wiki.redcactus.cloud/nl/blog",
        "https://wiki.redcactus.cloud/en/blog",
    ]

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    async def _fake_seed(url: str, **_kwargs: Any) -> CrawlResult:
        return _seed_result(url, bfs_links)

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    monkeypatch.setattr(crawl4ai_client, "crawl_page", _fake_seed)

    captured: dict[str, Any] = {}

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://wiki.redcactus.cloud",
            selector="main",
            max_pages=50,
            include_patterns=["/nl/"],
        )

    assert results == []
    payload = captured["payload"]
    urls_submitted = payload["urls"]
    # include_patterns is a substring filter — start_url with empty path
    # does NOT contain "/nl/" so it is correctly excluded. Every URL that
    # made it through MUST match.
    assert urls_submitted, "expected at least one /nl/ candidate to survive"
    for u in urls_submitted:
        assert "/nl/" in u, f"include_patterns leaked a non-/nl/ URL: {u}"
    assert "https://wiki.redcactus.cloud/nl/getting-started" in urls_submitted
    assert "https://wiki.redcactus.cloud/nl/advanced" in urls_submitted
    assert "https://wiki.redcactus.cloud/nl/blog" in urls_submitted
    assert "https://wiki.redcactus.cloud/en/getting-started" not in urls_submitted
    assert "https://wiki.redcactus.cloud/en/blog" not in urls_submitted
    assert "deep_crawl_strategy" not in payload["crawler_config"]["params"]
    # AC-4: every candidate produces an outcome record.
    assert len(outcomes) == len(urls_submitted)


@pytest.mark.asyncio
async def test_crawl_site_no_include_patterns_passes_all_same_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without include_patterns, every same-domain candidate reaches /crawl."""
    sitemap_urls = [
        "https://example.com/page-a",
        "https://example.com/page-b",
        "https://other.com/skipped",  # cross-domain, must be filtered out by candidate-set
    ]

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    async def _fake_seed(url: str, **_kwargs: Any) -> CrawlResult:
        return _seed_result(url, ["https://example.com/page-c"])

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    monkeypatch.setattr(crawl4ai_client, "crawl_page", _fake_seed)

    captured: dict[str, Any] = {}

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["payload"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
            include_patterns=None,
        )

    payload = captured["payload"]
    urls_submitted = payload["urls"]
    assert "https://example.com" in urls_submitted
    assert "https://example.com/page-a" in urls_submitted
    assert "https://example.com/page-b" in urls_submitted
    assert "https://example.com/page-c" in urls_submitted
    # Cross-domain entry from sitemap MUST NOT leak through.
    assert "https://other.com/skipped" not in urls_submitted
    assert "deep_crawl_strategy" not in payload["crawler_config"]["params"]
