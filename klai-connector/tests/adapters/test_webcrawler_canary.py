"""Tests for WebCrawlerAdapter Layer A (canary) and Layer B (login indicator).

SPEC-CRAWL-003: AC-2, AC-3, AC-4, AC-5, REQ-4–REQ-10.
All tests named after the SPEC Test Plan entries.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.webcrawler import CanaryMismatchError, WebCrawlerAdapter, _CrawlConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    """Create a minimal connector stub."""
    return SimpleNamespace(connector_id="conn-canary-001", config=config)


def _make_crawl_response(url: str, markdown: str, success: bool = True) -> dict[str, Any]:
    """Build a minimal Crawl4AI /crawl response for a single URL."""
    return {
        "results": [
            {
                "url": url,
                "success": success,
                "markdown": {"fit_markdown": markdown},
            }
        ]
    }


def _make_bfs_response(urls: list[str]) -> dict[str, Any]:
    """Build a minimal BFS task result."""
    return {
        "results": [
            {
                "url": url,
                "markdown": {
                    "fit_markdown": (
                        "# Article content\n\nThis is a real article with enough "
                        "text to pass the word count threshold and produce a valid "
                        "fingerprint for testing purposes."
                    )
                },
            }
            for url in urls
        ]
    }


@pytest.fixture
def adapter(mock_settings: Any) -> WebCrawlerAdapter:
    """WebCrawlerAdapter with mocked HTTP client."""
    a = WebCrawlerAdapter(mock_settings)
    a._http_client = MagicMock()
    return a


# ---------------------------------------------------------------------------
# Layer A: Canary fingerprint check
# ---------------------------------------------------------------------------


class TestCanaryMismatchError:
    """CanaryMismatchError exception class."""

    def test_canary_mismatch_error_attributes(self) -> None:
        """CanaryMismatchError stores all required attributes."""
        err = CanaryMismatchError(
            similarity=0.42,
            expected="abcdef1234567890",
            actual="0000000000000000",
            canary_url="https://wiki.example.com/known-page",
        )
        assert err.similarity == 0.42
        assert err.expected == "abcdef1234567890"
        assert err.actual == "0000000000000000"
        assert err.canary_url == "https://wiki.example.com/known-page"

    def test_canary_mismatch_error_str_contains_url(self) -> None:
        """CanaryMismatchError.__str__ mentions canary_url and similarity."""
        err = CanaryMismatchError(
            similarity=0.42,
            expected="abcdef1234567890",
            actual="0000000000000000",
            canary_url="https://wiki.example.com/known-page",
        )
        s = str(err)
        assert "wiki.example.com" in s
        assert "0.42" in s

    def test_canary_mismatch_error_is_exception(self) -> None:
        """CanaryMismatchError is a subclass of Exception."""
        err = CanaryMismatchError(
            similarity=0.0, expected="", actual="", canary_url="https://example.com"
        )
        assert isinstance(err, Exception)


class TestCanaryCheck:
    """Layer A: _crawl_canary and list_documents canary integration."""

    async def test_canary_check_passes_when_similarity_high(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_canary_check_passes_when_similarity_high — similarity=high, BFS proceeds."""
        # Use identical content so similarity = 1.0 (well above 0.80 threshold)
        article_content = (
            "# Known Good Article\n\nThis is the canonical reference article content "
            "that we use as a canary for detecting authentication expiry in the connector. "
            "It has more than twenty words to produce a valid fingerprint."
        )
        from app.services.content_fingerprint import compute_content_fingerprint
        stored_fp = compute_content_fingerprint(article_content)

        config = {
            "base_url": "https://wiki.example.com",
            "canary_url": "https://wiki.example.com/known-page",
            "canary_fingerprint": stored_fp,
            "max_pages": 10,
        }
        connector = _make_connector(config)

        # Canary crawl response (identical content → similarity = 1.0)
        canary_response = MagicMock()
        canary_response.raise_for_status = MagicMock()
        canary_response.json.return_value = _make_crawl_response(
            "https://wiki.example.com/known-page", article_content
        )

        # BFS crawl job
        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-canary-pass-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": _make_bfs_response(["https://wiki.example.com/page-1"]),
        }

        adapter._http_client.post = AsyncMock(
            side_effect=[canary_response, bfs_job_response]
        )
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            refs = await adapter.list_documents(connector)

        # BFS proceeded → at least one ref returned
        assert len(refs) >= 1

    async def test_canary_check_aborts_when_similarity_low(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_canary_check_aborts_when_similarity_low — similarity<0.80 raises CanaryMismatchError."""
        from app.services.content_fingerprint import compute_content_fingerprint

        # stored fingerprint = real article
        real_article = (
            "# Product Documentation\n\nThis guide explains how to configure the webcrawler "
            "connector for your knowledge base. You can set a canary URL to detect "
            "authentication expiry automatically."
        )
        stored_fp = compute_content_fingerprint(real_article)

        config = {
            "base_url": "https://wiki.example.com",
            "canary_url": "https://wiki.example.com/known-page",
            "canary_fingerprint": stored_fp,
            "max_pages": 10,
        }
        connector = _make_connector(config)

        # Live canary page now returns login wall (completely different content)
        login_wall = "Log in when you want to read this article. Please authenticate."

        canary_response = MagicMock()
        canary_response.raise_for_status = MagicMock()
        canary_response.json.return_value = _make_crawl_response(
            "https://wiki.example.com/known-page", login_wall
        )

        adapter._http_client.post = AsyncMock(return_value=canary_response)

        with pytest.raises(CanaryMismatchError) as exc_info:
            await adapter.list_documents(connector)

        err = exc_info.value
        assert err.canary_url == "https://wiki.example.com/known-page"
        assert err.similarity < 0.80
        assert err.expected == stored_fp

    async def test_canary_check_skipped_when_config_missing(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_canary_check_skipped_when_config_missing — no canary fields → BFS runs immediately."""
        config = {
            "base_url": "https://wiki.example.com",
            "max_pages": 10,
            # no canary_url, no canary_fingerprint
        }
        connector = _make_connector(config)

        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-no-canary-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": _make_bfs_response(["https://wiki.example.com/page-1"]),
        }

        adapter._http_client.post = AsyncMock(return_value=bfs_job_response)
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            refs = await adapter.list_documents(connector)

        # Only BFS POST call made (no canary POST)
        assert adapter._http_client.post.call_count == 1
        assert adapter._http_client.post.call_args_list[0][0][0].endswith("/crawl/job")
        assert len(refs) >= 1

    async def test_canary_check_skipped_when_only_url_set(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """canary_url without canary_fingerprint → canary skipped silently (REQ-6)."""
        config = {
            "base_url": "https://wiki.example.com",
            "canary_url": "https://wiki.example.com/known-page",
            # no canary_fingerprint
            "max_pages": 10,
        }
        connector = _make_connector(config)

        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-partial-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": _make_bfs_response(["https://wiki.example.com/page-1"]),
        }

        adapter._http_client.post = AsyncMock(return_value=bfs_job_response)
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            refs = await adapter.list_documents(connector)

        # No CanaryMismatchError raised, BFS ran
        assert len(refs) >= 1

    async def test_canary_network_failure_raises_canary_mismatch_error(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """If canary crawl returns empty results, CanaryMismatchError is raised with similarity=0."""

        stored_fp = "0123456789abcdef"
        config = {
            "base_url": "https://wiki.example.com",
            "canary_url": "https://wiki.example.com/known-page",
            "canary_fingerprint": stored_fp,
            "max_pages": 10,
        }
        connector = _make_connector(config)

        # Canary crawl returns empty results (network failure / 404)
        canary_response = MagicMock()
        canary_response.raise_for_status = MagicMock()
        canary_response.json.return_value = {"results": []}

        adapter._http_client.post = AsyncMock(return_value=canary_response)

        with pytest.raises(CanaryMismatchError) as exc_info:
            await adapter.list_documents(connector)

        assert exc_info.value.similarity == 0.0


# ---------------------------------------------------------------------------
# Layer B: Per-page login indicator
# ---------------------------------------------------------------------------


class TestLoginIndicatorWaitFor:
    """Layer B: login_indicator_selector in Phase 2 wait_for and auth-walled counting."""

    def test_login_indicator_appended_to_wait_for(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_login_indicator_appended_to_wait_for — wait_for includes CSS selector."""
        config = {
            "base_url": "https://wiki.example.com",
            "max_pages": 50,
            "login_indicator_selector": ".logged-in-user-menu",
        }
        cfg = _CrawlConfig.from_dict(config)
        params = adapter._build_page_crawl_params(cfg)

        wait_for = params.get("wait_for", "")
        assert ".logged-in-user-menu" in wait_for, (
            f"Expected login indicator CSS in wait_for, got: {wait_for!r}"
        )

    def test_no_login_indicator_wait_for_unchanged(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """Without login_indicator_selector, wait_for is the default JS predicate."""
        config = {
            "base_url": "https://wiki.example.com",
            "max_pages": 50,
        }
        cfg = _CrawlConfig.from_dict(config)
        params = adapter._build_page_crawl_params(cfg)

        wait_for = params.get("wait_for", "")
        assert "js:" in wait_for, f"Expected JS predicate in wait_for, got: {wait_for!r}"
        assert ".logged-in-user-menu" not in wait_for

    async def test_login_indicator_skips_failed_pages(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_login_indicator_skips_failed_pages — failed pages excluded from DocumentRefs."""
        # Build a response with some success=True and some success=False pages
        def _make_page(url: str, success: bool) -> dict[str, Any]:
            return {
                "url": url,
                "success": success,
                "markdown": {"fit_markdown": (
                    "# Content\n\nSome article text for testing purposes with "
                    "enough words to not be filtered out by word count."
                ) if success else "Log in to read this"},
            }

        ok_urls = [f"https://wiki.example.com/ok-{i}" for i in range(50)]
        fail_urls = [f"https://wiki.example.com/walled-{i}" for i in range(50)]

        config = {
            "base_url": "https://wiki.example.com",
            "login_indicator_selector": ".logged-in-user-menu",
            "max_pages": 200,
        }
        connector = _make_connector(config)

        # BFS discovery returns 100 URLs
        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-layer-b-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": {
                "results": [
                    _make_page(u, True) for u in ok_urls
                ] + [
                    _make_page(u, False) for u in fail_urls
                ]
            },
        }

        adapter._http_client.post = AsyncMock(return_value=bfs_job_response)
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            refs = await adapter.list_documents(connector)

        # Only the 50 successful pages should be in refs
        ref_urls = {ref.ref for ref in refs}
        for url in ok_urls:
            assert url in ref_urls, f"Expected {url} in refs"
        for url in fail_urls:
            assert url not in ref_urls, f"Expected {url} excluded from refs"

    async def test_login_indicator_populates_auth_walled_count(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """test_login_indicator_populates_auth_walled_count — adapter tracks auth_walled_count."""
        ok_urls = [f"https://wiki.example.com/ok-{i}" for i in range(50)]
        fail_urls = [f"https://wiki.example.com/walled-{i}" for i in range(50)]

        def _make_page(url: str, success: bool) -> dict[str, Any]:
            return {
                "url": url,
                "success": success,
                "markdown": {"fit_markdown": (
                    "# Content\n\nReal article text with enough words to pass "
                    "the minimum content threshold for ingestion."
                ) if success else "Log in to read"},
            }

        config = {
            "base_url": "https://wiki.example.com",
            "login_indicator_selector": ".logged-in-user-menu",
            "max_pages": 200,
        }
        connector = _make_connector(config)

        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-auth-count-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": {
                "results": [_make_page(u, True) for u in ok_urls]
                + [_make_page(u, False) for u in fail_urls]
            },
        }

        adapter._http_client.post = AsyncMock(return_value=bfs_job_response)
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            await adapter.list_documents(connector)

        assert adapter._auth_walled_count == 50, (
            f"Expected auth_walled_count=50, got {adapter._auth_walled_count}"
        )

    async def test_auth_walled_no_login_indicator_no_count(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """Without login_indicator_selector, failed pages are not auth-walled (not counted)."""
        config = {
            "base_url": "https://wiki.example.com",
            "max_pages": 10,
            # no login_indicator_selector
        }
        connector = _make_connector(config)

        bfs_job_response = MagicMock()
        bfs_job_response.raise_for_status = MagicMock()
        bfs_job_response.json.return_value = {"task_id": "task-no-indicator-001"}

        bfs_poll_response = MagicMock()
        bfs_poll_response.raise_for_status = MagicMock()
        bfs_poll_response.json.return_value = {
            "status": "completed",
            "result": _make_bfs_response(["https://wiki.example.com/page-1"]),
        }

        adapter._http_client.post = AsyncMock(return_value=bfs_job_response)
        adapter._http_client.get = AsyncMock(return_value=bfs_poll_response)

        with patch.object(adapter, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            await adapter.list_documents(connector)

        assert adapter._auth_walled_count == 0


# ---------------------------------------------------------------------------
# DocumentRef content_fingerprint field
# ---------------------------------------------------------------------------


class TestDocumentRefFingerprint:
    """Per-page content_fingerprint attached to DocumentRef (REQ-12)."""

    def test_process_results_attaches_content_fingerprint(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """_process_results attaches content_fingerprint to each DocumentRef."""
        data = {
            "results": [
                {
                    "url": "https://wiki.example.com/en/article",
                    "markdown": {
                        "fit_markdown": (
                            "# Article Content\n\nThis is a real article with more than "
                            "twenty words of content so it passes the fingerprint threshold."
                        )
                    },
                }
            ]
        }
        cache: dict[str, str] = {}
        refs = adapter._process_results(data, cache, base_url="https://wiki.example.com")

        assert len(refs) == 1
        assert hasattr(refs[0], "content_fingerprint"), (
            "DocumentRef must have content_fingerprint attribute"
        )
        assert len(refs[0].content_fingerprint) == 16, (
            f"Expected 16-char hex fingerprint, got: {refs[0].content_fingerprint!r}"
        )

    def test_process_results_short_page_empty_fingerprint(
        self, adapter: WebCrawlerAdapter,
    ) -> None:
        """Short pages (<20 words) get empty content_fingerprint (REQ-12)."""
        data = {
            "results": [
                {
                    "url": "https://wiki.example.com/short",
                    "markdown": {"fit_markdown": "# Short\n\nToo few words."},
                }
            ]
        }
        cache: dict[str, str] = {}
        refs = adapter._process_results(data, cache, base_url="https://wiki.example.com")

        assert len(refs) == 1
        assert refs[0].content_fingerprint == "", (
            f"Expected empty fingerprint for short page, got: {refs[0].content_fingerprint!r}"
        )
