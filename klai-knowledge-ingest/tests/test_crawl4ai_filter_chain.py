"""crawl_site frontier filtering and Crawl4AI payload shape tests."""

from __future__ import annotations

from pathlib import Path
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
async def test_fetch_seed_retries_relaxed_config_after_minimal_content_antibot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header-only portfolio pages should not be dropped by chrome stripping."""
    calls: list[dict[str, Any]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(payload["crawler_config"]["params"])
        if len(calls) == 1:
            request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
            response = httpx.Response(
                500,
                json={
                    "detail": "Blocked by anti-bot protection: "
                    "Structural: minimal_text, 0 chars visible"
                },
                request=request,
            )
            raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)
        return {
            "results": [
                {
                    "url": "https://jantinedoornbos.nl/",
                    "success": True,
                    "markdown": "Header content is visible after relaxed retry",
                    "links": {"internal": []},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    result = await crawl4ai_client._fetch_seed_page(
        start_url="https://jantinedoornbos.nl/",
        crawler_config=crawl4ai_client.build_crawl_config(None),
        cookies=None,
    )

    assert result.success is True
    assert len(calls) == 2
    # Chrome stripping lives inside wait_for's prep on >= 0.9-compatible
    # configs; the relaxed retry drops wait_for (and the stripping with it).
    assert "js_code_before_wait" not in calls[0]
    assert "wait_for" in calls[0]
    assert "el.remove()" in calls[0]["wait_for"]
    assert "wait_for" not in calls[1]
    assert calls[1]["excluded_tags"] == ["script", "style"]


@pytest.mark.asyncio
async def test_crawl_page_retries_relaxed_config_when_strict_result_is_thin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview-style single-page crawls should recover when pruning hides content."""
    calls: list[dict[str, Any]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(payload["crawler_config"]["params"])
        if len(calls) == 1:
            return {
                "results": [
                    {
                        "url": "https://example.com/",
                        "success": True,
                        "html": "<html><body>" + ("real content " * 140) + "</body></html>",
                        "markdown": "thin preview text",
                        "links": {"internal": []},
                    }
                ]
            }
        return {
            "results": [
                {
                    "url": "https://example.com/",
                    "success": True,
                    "html": "<html><body>" + ("real content " * 140) + "</body></html>",
                    "markdown": "recovered content " * 120,
                    "links": {"internal": []},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    result = await crawl4ai_client.crawl_page(
        "https://example.com/",
        retry_relaxed_on_thin=True,
    )

    assert result.word_count >= 100
    assert len(calls) == 2
    assert "el.remove()" in calls[0]["wait_for"]
    assert "wait_for" not in calls[1]
    assert calls[1]["excluded_tags"] == ["script", "style"]


@pytest.mark.asyncio
async def test_crawl_site_recovers_thin_bulk_pages_with_relaxed_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1: a BFS child page that is thin under the strict config but content-rich
    in HTML must be recovered with the relaxed config — so site ingest matches
    what the single-page preview shows instead of silently ingesting it thin."""
    _patch_seed(
        monkeypatch,
        _seed_result(
            "https://portfolio.example",
            ["https://portfolio.example/work"],
        ),
    )

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    rich_html = "<html><body>" + ("real content " * 140) + "</body></html>"
    bulk_configs: list[dict[str, Any]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        params = payload["crawler_config"]["params"]
        bulk_configs.append(params)
        relaxed = "wait_for" not in params
        return {
            "results": [
                {
                    "url": "https://portfolio.example/work",
                    "success": True,
                    "html": rich_html,
                    "markdown": ("recovered content " * 120) if relaxed else "thin",
                    "links": {"internal": []},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    results, _outcomes = await crawl4ai_client.crawl_site(
        start_url="https://portfolio.example",
        selector=None,
        max_pages=10,
    )

    work = next(r for r in results if r.url == "https://portfolio.example/work")
    assert work.word_count >= 100  # recovered, not the 1-word strict result
    # Strict bulk fetch + one relaxed retry over the thin page.
    assert len(bulk_configs) == 2
    assert "el.remove()" in bulk_configs[0]["wait_for"]
    assert "wait_for" not in bulk_configs[1]


@pytest.mark.asyncio
async def test_crawl_site_discovers_links_from_recovered_bulk_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovered child pages must feed the BFS frontier, not only storage."""
    _patch_seed(
        monkeypatch,
        _seed_result(
            "https://portfolio.example",
            ["https://portfolio.example/work"],
        ),
    )

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    rich_html = "<html><body>" + ("real content " * 140) + "</body></html>"
    submitted_batches: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        urls = payload["urls"]
        submitted_batches.append(urls)
        params = payload["crawler_config"]["params"]
        relaxed = "wait_for" not in params

        if urls == ["https://portfolio.example/work"] and not relaxed:
            return {
                "results": [
                    {
                        "url": "https://portfolio.example/work",
                        "success": True,
                        "html": rich_html,
                        "markdown": "thin",
                        "links": {"internal": []},
                    }
                ]
            }
        if urls == ["https://portfolio.example/work"] and relaxed:
            return {
                "results": [
                    {
                        "url": "https://portfolio.example/work",
                        "success": True,
                        "html": rich_html,
                        "markdown": "recovered content " * 120,
                        "links": {
                            "internal": [
                                {"href": "https://portfolio.example/work/detail", "text": "Detail"}
                            ]
                        },
                    }
                ]
            }
        return {
            "results": [
                {
                    "url": url,
                    "success": True,
                    "html": "<html><body>detail</body></html>",
                    "markdown": "detail content " * 120,
                    "links": {"internal": []},
                }
                for url in urls
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    results, _outcomes = await crawl4ai_client.crawl_site(
        start_url="https://portfolio.example",
        selector=None,
        max_pages=10,
    )

    assert ["https://portfolio.example/work/detail"] in submitted_batches
    assert "https://portfolio.example/work/detail" in {r.url for r in results}


@pytest.mark.asyncio
async def test_crawl_site_skips_relaxed_retry_when_selector_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user/stored selector means the thin result is intentional — no relaxed
    retry (which would re-run the same scoped extraction and waste a crawl)."""
    _patch_seed(
        monkeypatch,
        _seed_result(
            "https://portfolio.example",
            ["https://portfolio.example/work"],
        ),
    )

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    bulk_calls = 0

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal bulk_calls
        bulk_calls += 1
        return {
            "results": [
                {
                    "url": "https://portfolio.example/work",
                    "success": True,
                    "html": "<html><body>" + ("real content " * 140) + "</body></html>",
                    "markdown": "thin",
                    "links": {"internal": []},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    await crawl4ai_client.crawl_site(
        start_url="https://portfolio.example",
        selector="main",
        max_pages=10,
    )

    # Exactly one bulk fetch — no relaxed retry pass when a selector is active.
    assert bulk_calls == 1


def test_dom_summary_excludes_non_content_nodes() -> None:
    source = Path(crawl4ai_client.__file__).read_text(encoding="utf-8")
    assert "skipTags" in source
    assert "'style'" in source
    assert "'script'" in source
    assert "window.getComputedStyle" in source


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
    _patch_seed(
        monkeypatch,
        _seed_result(
            "https://wiki.redcactus.cloud",
            [
                "https://wiki.redcactus.cloud/nl/blog",
                "https://wiki.redcactus.cloud/en/blog",
            ],
        ),
    )

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

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
    assert "https://wiki.redcactus.cloud/nl/blog" in urls_submitted
    assert "https://wiki.redcactus.cloud/en/getting-started" not in urls_submitted
    assert "https://wiki.redcactus.cloud/en/blog" not in urls_submitted
    assert "deep_crawl_strategy" not in payload["crawler_config"]["params"]
    assert len(outcomes) == 1 + len(urls_submitted)


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
    _patch_seed(monkeypatch, _seed_result("https://example.com", ["https://example.com/page-c"]))

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
    assert "https://example.com/page-c" in urls_submitted
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

    async def _fake_sitemap(_base: str) -> list[str]:
        return sitemap_urls

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(
        monkeypatch,
        _seed_result(
            "https://www.getklai.com/blog",
            [
                "https://www.getklai.com/blog/article-from-bfs",
                "https://www.getklai.com/blog/tag/AI",
            ],
        ),
    )

    captured: dict[str, Any] = {}

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["payload"] = kwargs.get("json")
        urls = captured["payload"]["urls"]
        results = [
            {
                "url": u,
                "success": True,
                "status_code": 200,
                "html": "<html>Article</html>",
                "markdown": "Article",
                "links": {"internal": []},
                "media": {},
            }
            for u in urls
        ]
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": results}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://www.getklai.com/blog",
            include_patterns=["/blog/*"],
            exclude_patterns=["/blog/tag/*"],
        )

    assert {r.url for r in results} == {
        "https://www.getklai.com/blog",
        "https://www.getklai.com/blog/article-from-bfs",
        "https://www.getklai.com/blog/article-from-sitemap",
    }
    assert {o["url"] for o in outcomes} == {
        "https://www.getklai.com/blog",
        "https://www.getklai.com/blog/article-from-bfs",
        "https://www.getklai.com/blog/article-from-sitemap",
    }
    assert captured["payload"]["urls"] == [
        "https://www.getklai.com/blog/article-from-sitemap",
        "https://www.getklai.com/blog/article-from-bfs",
    ]


@pytest.mark.asyncio
async def test_bfs_deep_crawl_pushes_exclude_patterns_into_filter_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BFS must not fetch archive URLs that are excluded from ingest."""
    captured: dict[str, Any] = {}

    async def _fake_sleep(_seconds: float) -> None:
        return None

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["payload"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"task_id": "crawl_test"}, request=request)

    async def _fake_get(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"status": "completed", "result": {"results": []}},
            request=request,
        )

    monkeypatch.setattr(crawl4ai_client.asyncio, "sleep", _fake_sleep)
    with (
        patch("httpx.AsyncClient.post", new=_fake_post),
        patch("httpx.AsyncClient.get", new=_fake_get),
    ):
        await crawl4ai_client._bfs_deep_crawl(
            start_url="https://www.getklai.com/blog",
            crawler_config=crawl4ai_client.build_crawl_config(selector=None),
            max_depth=3,
            max_pages=200,
            include_patterns=["/blog/*"],
            exclude_patterns=["/blog/tag/*", "/blog/category/*"],
            cookies=None,
        )

    strategy = captured["payload"]["crawler_config"]["params"]["deep_crawl_strategy"]
    filters = strategy["params"]["filter_chain"]["params"]["filters"]
    assert filters == [
        {
            "type": "URLPatternFilter",
            "params": {"patterns": ["/blog/*"]},
        },
        {
            "type": "URLPatternFilter",
            "params": {
                "patterns": ["/blog/tag/*", "/blog/category/*"],
                "reverse": True,
            },
        },
    ]
