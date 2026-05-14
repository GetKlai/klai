"""SPEC-INGEST-RECONCILE-001 Fix 1 — include_patterns filtering on candidates.

The legacy test (pre-Reconcile-001) asserted that ``filter_chain`` was
JSON-wrapped correctly inside crawl4ai's ``deep_crawl_strategy`` payload.
That payload no longer exists: ``crawl_site`` now hands a flat URL list
to ``POST /crawl`` and applies ``include_patterns`` as a candidate-side
substring filter. These tests pin the new behaviour:

- When ``include_patterns`` is set, only matching candidates reach the
  bulk request body.
- When ``include_patterns`` is None, every same-domain candidate from
  sitemap + BFS-union reaches the request body — but ``start_url`` is
  NOT in the bulk submission (it is fetched separately as the seed; see
  followup PR §"redundant start_url fetch").
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


def _patch_seed(monkeypatch: pytest.MonkeyPatch, seed_result: CrawlResult) -> None:
    """Stub ``_fetch_seed_page`` so the seed call doesn't hit httpx.

    The bulk path (the focus of these tests) still goes through the real
    ``httpx.AsyncClient.post`` so the test can capture its payload.
    """

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return seed_result

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)


@pytest.mark.asyncio
async def test_crawl_site_applies_include_patterns_to_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_patterns acts as a substring filter on the bulk candidate set."""
    sitemap_urls = [
        "https://wiki.redcactus.cloud/nl/getting-started",
        "https://wiki.redcactus.cloud/en/getting-started",
        "https://wiki.redcactus.cloud/nl/advanced",
    ]
    bfs_results = [
        _seed_result("https://wiki.redcactus.cloud", []),
        _seed_result("https://wiki.redcactus.cloud/nl/blog", []),
    ]

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_bfs(**_kwargs: Any) -> tuple[list[CrawlResult], None]:
        return bfs_results, None

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)

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

    # Seed (start_url) is included in results because the synthesised seed
    # is success + has content + same-domain.
    assert any(r.url == "https://wiki.redcactus.cloud" for r in results)

    payload = captured["payload"]
    urls_submitted = payload["urls"]
    # include_patterns is a substring filter — start_url with empty path
    # does not match "/nl/" anyway, but the more important guard is that
    # it never reaches the bulk request because the seed already covered it.
    assert "https://wiki.redcactus.cloud" not in urls_submitted, (
        "start_url must not be in the bulk submission (it is the seed)"
    )
    for u in urls_submitted:
        assert "/nl/" in u, f"include_patterns leaked a non-/nl/ URL: {u}"
    assert "https://wiki.redcactus.cloud/nl/getting-started" in urls_submitted
    assert "https://wiki.redcactus.cloud/nl/advanced" in urls_submitted
    assert "https://wiki.redcactus.cloud/nl/blog" not in urls_submitted
    assert "https://wiki.redcactus.cloud/en/getting-started" not in urls_submitted
    assert "https://wiki.redcactus.cloud/en/blog" not in urls_submitted
    assert "deep_crawl_strategy" not in payload["crawler_config"]["params"]
    # AC-4: every candidate produces an outcome record. BFS + supplement.
    assert len(outcomes) == len(bfs_results) + len(urls_submitted)


@pytest.mark.asyncio
async def test_crawl_site_no_include_patterns_passes_all_same_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without include_patterns, every same-domain non-seed candidate reaches /crawl."""
    sitemap_urls = [
        "https://example.com/page-a",
        "https://example.com/page-b",
        "https://other.com/skipped",  # cross-domain, must be filtered out
    ]

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_bfs(**_kwargs: Any) -> tuple[list[CrawlResult], None]:
        return [
            _seed_result("https://example.com", []),
            _seed_result("https://example.com/page-c", []),
        ], None

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)

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
    # start_url is the seed — not in the bulk submission.
    assert "https://example.com" not in urls_submitted, (
        "start_url must not be in the bulk submission (it is the seed)"
    )
    assert "https://example.com/page-a" in urls_submitted
    assert "https://example.com/page-b" in urls_submitted
    assert "https://example.com/page-c" not in urls_submitted
    # Cross-domain entry from sitemap MUST NOT leak through.
    assert "https://other.com/skipped" not in urls_submitted
    assert "deep_crawl_strategy" not in payload["crawler_config"]["params"]


@pytest.mark.asyncio
async def test_crawl_site_excludes_archive_candidates_after_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """exclude_patterns removes archive/listing pages before ingest accounting."""
    sitemap_urls = [
        "https://www.getklai.com/blog/article-from-sitemap",
        "https://www.getklai.com/blog/tag/privacy",
    ]
    bfs_results = [
        CrawlResult(
            url="https://www.getklai.com/blog/article-from-bfs",
            fit_markdown="Article",
            raw_markdown="Article",
            html="<html></html>",
            word_count=1,
            success=True,
            links={"internal": []},
        ),
        CrawlResult(
            url="https://www.getklai.com/blog/tag/AI",
            fit_markdown="Tag archive",
            raw_markdown="Tag archive",
            html="<html></html>",
            word_count=2,
            success=True,
            links={"internal": []},
        ),
    ]

    async def _fake_bfs(**_kwargs: Any) -> tuple[list[CrawlResult], None]:
        return bfs_results, None

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)
    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    captured: dict[str, Any] = {}

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["payload"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://www.getklai.com/blog",
            include_patterns=["/blog/*"],
            exclude_patterns=["/blog/tag/*"],
        )

    assert {r.url for r in results} == {"https://www.getklai.com/blog/article-from-bfs"}
    assert {o["url"] for o in outcomes} == {
        "https://www.getklai.com/blog/article-from-bfs",
        "https://www.getklai.com/blog/article-from-sitemap",
    }
    assert captured["payload"]["urls"] == ["https://www.getklai.com/blog/article-from-sitemap"]
