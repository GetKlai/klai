"""Tests for WebCrawlerAdapter — SPEC-CRAWL-002: Two-Phase Discovery + Extraction."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.webcrawler import WebCrawlerAdapter, _CrawlConfig

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(connector_id="conn-webcrawl-001", config=config)


def _bfs_result(urls: list[str]) -> dict[str, Any]:
    """Build a minimal Crawl4AI BFS task result with markdown for each URL."""
    return {
        "results": [
            {
                "url": url,
                "markdown": {"fit_markdown": f"# Content for {url}\n\nSome text here for testing purposes."},
            }
            for url in urls
        ]
    }


def _sync_crawl_response(urls: list[str]) -> dict[str, Any]:
    """Build a minimal Crawl4AI /crawl sync response."""
    return {
        "results": [
            {
                "url": url,
                "markdown": {"fit_markdown": f"# Extracted for {url}\n\nSelector-matched content."},
            }
            for url in urls
        ]
    }


@pytest.fixture
def adapter(mock_settings: Any) -> WebCrawlerAdapter:
    adapter = WebCrawlerAdapter(mock_settings)
    # Replace the real httpx client with a mock so no network calls are made.
    adapter._http_client = MagicMock()
    return adapter


# ---------------------------------------------------------------------------
# 1. Two-phase flow with content_selector
# ---------------------------------------------------------------------------


async def test_list_documents_with_selector_calls_two_phases(adapter: WebCrawlerAdapter) -> None:
    """AC-1 / AC-6: BFS phase runs first; extraction phase re-crawls with selector."""
    discovered_urls = [
        "https://wiki.example.com/en/crm-software/act",
        "https://wiki.example.com/en/crm-software/salesforce",
    ]
    config = {
        "base_url": "https://wiki.example.com",
        "path_prefix": "/en",
        "content_selector": ".tab-structure",
        "max_pages": 100,
    }
    connector = _make_connector(config)

    # Mock POST /crawl/job → returns task_id
    job_post_response = MagicMock()
    job_post_response.raise_for_status = MagicMock()
    job_post_response.json.return_value = {"task_id": "task-bfs-001"}

    # Mock GET /crawl/job/{task_id} → completed with BFS results
    poll_response = MagicMock()
    poll_response.raise_for_status = MagicMock()
    poll_response.json.return_value = {
        "status": "completed",
        "result": _bfs_result(discovered_urls),
    }

    # Mock POST /crawl (sync extraction) → selector-matched content
    sync_post_response = MagicMock()
    sync_post_response.raise_for_status = MagicMock()
    sync_post_response.json.return_value = _sync_crawl_response(discovered_urls)

    adapter._http_client.post = AsyncMock(side_effect=[job_post_response, sync_post_response])
    adapter._http_client.get = AsyncMock(return_value=poll_response)

    with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
        refs = await adapter.list_documents(connector)

    assert len(refs) == 2
    # Phase 2 content should be used (not Phase 1 BFS markdown).
    assert all("Extracted for" in adapter._crawl_cache["conn-webcrawl-001"][ref.ref] for ref in refs)

    # Verify call sequence: first POST to /crawl/job, second POST to /crawl.
    assert adapter._http_client.post.call_count == 2
    first_call_url = adapter._http_client.post.call_args_list[0][0][0]
    second_call_url = adapter._http_client.post.call_args_list[1][0][0]
    assert first_call_url.endswith("/crawl/job")
    assert second_call_url.endswith("/crawl")

    # GET must have been called for polling.
    assert adapter._http_client.get.call_count >= 1


# ---------------------------------------------------------------------------
# 2. No content_selector → single phase, no re-crawl
# ---------------------------------------------------------------------------


async def test_list_documents_without_selector_skips_extraction(adapter: WebCrawlerAdapter) -> None:
    """AC-2: When no content_selector, only Phase 1 BFS runs — no /crawl call."""
    discovered_urls = ["https://wiki.example.com/en/page-one"]
    config = {
        "base_url": "https://wiki.example.com",
        "max_pages": 50,
    }
    connector = _make_connector(config)

    job_post_response = MagicMock()
    job_post_response.raise_for_status = MagicMock()
    job_post_response.json.return_value = {"task_id": "task-bfs-002"}

    poll_response = MagicMock()
    poll_response.raise_for_status = MagicMock()
    poll_response.json.return_value = {
        "status": "completed",
        "result": _bfs_result(discovered_urls),
    }

    adapter._http_client.post = AsyncMock(return_value=job_post_response)
    adapter._http_client.get = AsyncMock(return_value=poll_response)

    with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
        refs = await adapter.list_documents(connector)

    assert len(refs) == 1
    # Only one POST call: /crawl/job. No /crawl (sync extraction).
    assert adapter._http_client.post.call_count == 1
    assert adapter._http_client.post.call_args_list[0][0][0].endswith("/crawl/job")


# ---------------------------------------------------------------------------
# 3. _build_discovery_params has no css_selector
# ---------------------------------------------------------------------------


def test_build_discovery_params_no_selector(adapter: WebCrawlerAdapter) -> None:
    """Discovery params must not contain css_selector or PruningContentFilter."""
    params = adapter._build_discovery_params()

    assert "css_selector" not in params
    md_gen = params.get("markdown_generator", {})
    md_params = md_gen.get("params", {})
    assert "content_filter" not in md_params
    assert params["word_count_threshold"] == 0


# ---------------------------------------------------------------------------
# 4. URLPatternFilter constructed as path-only wildcard pattern
# ---------------------------------------------------------------------------


async def test_start_crawl_injects_path_prefix_filter(adapter: WebCrawlerAdapter) -> None:
    """AC-3: URLPatternFilter pattern = '/' + path_prefix.strip('/') + '/*'."""
    config = {
        "base_url": "https://wiki.example.com",
        "path_prefix": "/en",
        "max_pages": 100,
    }

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"task_id": "task-filter-001"}

    captured_payload: dict[str, Any] = {}

    async def capture_post(url: str, **kwargs: Any) -> MagicMock:
        captured_payload.update(kwargs.get("json", {}))
        return post_response

    adapter._http_client.post = capture_post  # type: ignore[method-assign]

    cfg = _CrawlConfig.from_dict(config)
    crawl_params = adapter._build_discovery_params()
    await adapter._start_crawl(cfg, crawl_params)

    deep_strategy = captured_payload["crawler_config"]["params"]["deep_crawl_strategy"]
    filter_chain = deep_strategy["params"]["filter_chain"]
    assert filter_chain["type"] == "FilterChain"
    filters = filter_chain["params"]["filters"]
    assert len(filters) == 1
    assert filters[0]["type"] == "URLPatternFilter"
    assert filters[0]["params"]["patterns"] == ["/en/*"]


# ---------------------------------------------------------------------------
# 5. No filter_chain when path_prefix is empty
# ---------------------------------------------------------------------------


async def test_start_crawl_no_path_prefix_no_filter(adapter: WebCrawlerAdapter) -> None:
    """When path_prefix is empty, no filter_chain is added to deep_crawl_strategy."""
    config = {
        "base_url": "https://wiki.example.com",
        "max_pages": 50,
    }

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"task_id": "task-nofilter-001"}

    captured_payload: dict[str, Any] = {}

    async def capture_post(url: str, **kwargs: Any) -> MagicMock:
        captured_payload.update(kwargs.get("json", {}))
        return post_response

    adapter._http_client.post = capture_post  # type: ignore[method-assign]

    cfg = _CrawlConfig.from_dict(config)
    crawl_params = adapter._build_discovery_params()
    await adapter._start_crawl(cfg, crawl_params)

    deep_strategy = captured_payload["crawler_config"]["params"]["deep_crawl_strategy"]
    assert "filter_chain" not in deep_strategy["params"]


# ---------------------------------------------------------------------------
# 6. Cookies injected in Phase 1 (BFS payload)
# ---------------------------------------------------------------------------


async def test_start_crawl_injects_cookies(adapter: WebCrawlerAdapter) -> None:
    """AC-4: payload['hooks'] contains the cookie hook when cookies are given."""
    config = {"base_url": "https://wiki.example.com", "max_pages": 50}
    cookies = [{"name": "session", "value": "abc123", "domain": "wiki.example.com"}]

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"task_id": "task-cookies-001"}

    captured_payload: dict[str, Any] = {}

    async def capture_post(url: str, **kwargs: Any) -> MagicMock:
        captured_payload.update(kwargs.get("json", {}))
        return post_response

    adapter._http_client.post = capture_post  # type: ignore[method-assign]

    cfg = _CrawlConfig.from_dict(config)
    crawl_params = adapter._build_discovery_params()
    await adapter._start_crawl(cfg, crawl_params, cookies=cookies)

    assert "hooks" in captured_payload
    hook_code = captured_payload["hooks"]["code"]["on_page_context_created"]
    # Verify the cookies JSON is embedded in the hook code.
    assert json.dumps(cookies) in hook_code


# ---------------------------------------------------------------------------
# 7. Phase 2 replaces Phase 1 content in cache
# ---------------------------------------------------------------------------


async def test_phase2_replaces_phase1_content(adapter: WebCrawlerAdapter) -> None:
    """AC-6: Final refs reflect Phase 2 extraction content, not Phase 1 BFS markdown."""
    discovered_urls = ["https://wiki.example.com/en/page-a"]
    config = {
        "base_url": "https://wiki.example.com",
        "content_selector": ".tab-structure",
        "max_pages": 50,
    }
    connector = _make_connector(config)

    job_post_response = MagicMock()
    job_post_response.raise_for_status = MagicMock()
    job_post_response.json.return_value = {"task_id": "task-replace-001"}

    poll_response = MagicMock()
    poll_response.raise_for_status = MagicMock()
    poll_response.json.return_value = {
        "status": "completed",
        "result": _bfs_result(discovered_urls),
    }

    # Phase 2 returns distinctly different content so we can verify replacement.
    extraction_content = "# EXTRACTED\n\nTab-structure content only."
    sync_post_response = MagicMock()
    sync_post_response.raise_for_status = MagicMock()
    sync_post_response.json.return_value = {
        "results": [
            {"url": discovered_urls[0], "markdown": {"fit_markdown": extraction_content}},
        ]
    }

    adapter._http_client.post = AsyncMock(side_effect=[job_post_response, sync_post_response])
    adapter._http_client.get = AsyncMock(return_value=poll_response)

    with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
        refs = await adapter.list_documents(connector)

    assert len(refs) == 1
    cached = adapter._crawl_cache["conn-webcrawl-001"][refs[0].ref]
    assert cached == extraction_content


# ---------------------------------------------------------------------------
# 8. Sitemap supplement runs after BFS phases
# ---------------------------------------------------------------------------


async def test_process_results_extracts_media_images(adapter: WebCrawlerAdapter) -> None:
    """Images from media['images'] are stored in DocumentRef.images — bypasses PruningContentFilter."""
    url = "https://wiki.example.com/en/page-with-images"
    data = {
        "results": [
            {
                "url": url,
                # fit_markdown has no ![alt](url) — PruningContentFilter stripped them.
                "markdown": {"fit_markdown": "# Page title\n\nSome article text without images."},
                "media": {
                    "images": [
                        {"src": "https://wiki.example.com/uploads/screenshot1.png", "alt": "Screenshot 1"},
                        {"src": "https://wiki.example.com/uploads/screenshot2.png", "alt": ""},
                        {"src": "", "alt": "empty src should be skipped"},
                    ]
                },
            }
        ]
    }
    cache: dict = {}
    refs = adapter._process_results(data, cache, base_url="https://wiki.example.com")

    assert len(refs) == 1
    assert refs[0].images is not None
    assert len(refs[0].images) == 2  # empty src skipped
    assert refs[0].images[0].url == "https://wiki.example.com/uploads/screenshot1.png"
    assert refs[0].images[0].alt == "Screenshot 1"
    assert refs[0].images[1].url == "https://wiki.example.com/uploads/screenshot2.png"
    assert refs[0].images[1].alt == ""


async def test_process_results_no_media_images_is_none(adapter: WebCrawlerAdapter) -> None:
    """DocumentRef.images is None when crawl4ai returns no media images."""
    url = "https://wiki.example.com/en/text-only-page"
    data = {
        "results": [
            {
                "url": url,
                "markdown": {"fit_markdown": "# Text only\n\nNo images here."},
            }
        ]
    }
    cache: dict = {}
    refs = adapter._process_results(data, cache, base_url="https://wiki.example.com")

    assert len(refs) == 1
    assert refs[0].images is None


async def test_sitemap_supplement_still_runs(adapter: WebCrawlerAdapter) -> None:
    """AC-7: Sitemap URLs are crawled after BFS phases, up to max_pages."""
    bfs_urls = [f"https://wiki.example.com/bfs-{i}" for i in range(3)]
    sitemap_extra = [f"https://wiki.example.com/sitemap-{i}" for i in range(5)]
    config = {
        "base_url": "https://wiki.example.com",
        "max_pages": 10,
    }
    connector = _make_connector(config)

    job_post_response = MagicMock()
    job_post_response.raise_for_status = MagicMock()
    job_post_response.json.return_value = {"task_id": "task-sitemap-001"}

    poll_response = MagicMock()
    poll_response.raise_for_status = MagicMock()
    poll_response.json.return_value = {
        "status": "completed",
        "result": _bfs_result(bfs_urls),
    }

    # Sitemap supplement /crawl response.
    sitemap_crawl_response = MagicMock()
    sitemap_crawl_response.raise_for_status = MagicMock()
    sitemap_crawl_response.json.return_value = _sync_crawl_response(sitemap_extra)

    adapter._http_client.post = AsyncMock(side_effect=[job_post_response, sitemap_crawl_response])
    adapter._http_client.get = AsyncMock(return_value=poll_response)

    with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=sitemap_extra)):
        refs = await adapter.list_documents(connector)

    # 3 BFS + 5 sitemap = 8 total (within max_pages=10).
    assert len(refs) == 8
    ref_urls = {ref.ref for ref in refs}
    assert all(u in ref_urls for u in bfs_urls)
    assert all(u in ref_urls for u in sitemap_extra)
