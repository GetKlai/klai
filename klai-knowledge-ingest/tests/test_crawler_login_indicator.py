"""Tests for the login-indicator auth-wall guard (SPEC-CRAWLER-004 Fase B).

Covers REQ-02.3 and AC-02.3:
- When ``login_indicator_selector`` is set and crawl4ai flags a page as
  ``success=False``, ``run_crawl_job`` halts the BFS and writes exactly
  one ``crawl_jobs`` row with ``status='failed'`` and
  ``error='auth_wall_detected: {selector}'``.
- No follow-up pages are ingested after the first auth-walled page.
- The wait_for JS built by ``build_crawl_config`` negates the selector
  so crawl4ai fails closed when the indicator is present.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import (
    AuthWallDetected,
    _ingest_crawl_result,
    run_crawl_job,
)
from knowledge_ingest.crawl4ai_client import CrawlResult, build_crawl_config


class TestBuildCrawlConfigWithLoginIndicator:
    """The config builder injects the selector into the wait_for expression."""

    def test_no_login_indicator_uses_base_wait(self) -> None:
        cfg = build_crawl_config(selector=None)
        assert "querySelector" not in cfg["wait_for"]

    def test_login_indicator_negates_wait_for(self) -> None:
        cfg = build_crawl_config(selector=None, login_indicator_selector="#login-form")
        wait = cfg["wait_for"]
        assert "!document.querySelector('#login-form')" in wait
        # Base word-count condition must still be ANDed in.
        assert "innerText.trim().split" in wait

    def test_single_quote_in_selector_is_escaped(self) -> None:
        """A selector with a single quote must not break out of the JS string."""
        cfg = build_crawl_config(
            selector=None,
            login_indicator_selector="a[title='Log in']",
        )
        # We escape single quotes so the injected JS remains well-formed.
        assert r"a[title=\'Log in\']" in cfg["wait_for"]


class TestIngestCrawlResultAuthWall:
    """_ingest_crawl_result raises AuthWallDetected when appropriate."""

    @pytest.mark.asyncio()
    async def test_failed_result_with_selector_raises_auth_wall(self) -> None:
        failed = CrawlResult(
            url="https://wiki.example/page",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="wait_for timeout",
        )
        with pytest.raises(AuthWallDetected) as excinfo:
            await _ingest_crawl_result(
                failed,
                url=failed.url,
                org_id="org",
                kb_slug="support",
                login_indicator_selector="#login-form",
            )
        assert "auth_wall_detected" in str(excinfo.value)
        assert excinfo.value.selector == "#login-form"

    @pytest.mark.asyncio()
    async def test_failed_result_without_selector_raises_generic(self) -> None:
        failed = CrawlResult(
            url="https://example/page",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="network timeout",
        )
        with pytest.raises(ValueError, match="Crawl failed"):
            await _ingest_crawl_result(
                failed,
                url=failed.url,
                org_id="org",
                kb_slug="support",
                login_indicator_selector=None,
            )


class TestRunCrawlJobAuthWall:
    """run_crawl_job halts on the first auth-walled page and writes a failed row."""

    @pytest.mark.asyncio()
    async def test_auth_wall_halts_bfs_and_fails_job(self) -> None:
        # crawl_site returns a happy first page followed by an auth-walled one.
        # After the auth wall fires, we must not attempt to ingest the 3rd page.
        happy = CrawlResult(
            url="https://wiki.example/a",
            fit_markdown="content",
            raw_markdown="content",
            html="<html></html>",
            word_count=10,
            success=True,
        )
        walled = CrawlResult(
            url="https://wiki.example/b",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
        )
        never_reached = CrawlResult(
            url="https://wiki.example/c",
            fit_markdown="content",
            raw_markdown="content",
            html="<html></html>",
            word_count=10,
            success=True,
        )

        pool = MagicMock()
        pool.execute = AsyncMock(return_value=None)

        with (
            patch(
                "knowledge_ingest.adapters.crawler.crawl_site",
                new=AsyncMock(return_value=[happy, walled, never_reached]),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.get_pool",
                new=AsyncMock(return_value=pool),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "knowledge_ingest.adapters.crawler._ingest_crawl_result",
                new=AsyncMock(return_value=None),
            ) as ingest_mock,
        ):
            await run_crawl_job(
                job_id="job-1",
                org_id="org",
                kb_slug="support",
                start_url="https://wiki.example",
                login_indicator_selector="#login-form",
            )

        # The happy page must have been ingested exactly once.
        assert ingest_mock.call_count == 1
        ingest_mock.assert_awaited_with(
            happy,
            happy.url,
            "org",
            "support",
            pool=pool,
            stored=None,
            login_indicator_selector="#login-form",
        )

        # Verify at least one UPDATE marked the job as failed with auth_wall_detected.
        update_calls = pool.execute.await_args_list
        failed_updates = [
            c
            for c in update_calls
            if len(c.args) >= 3
            and isinstance(c.args[0], str)
            and "UPDATE knowledge.crawl_jobs" in c.args[0]
            and "status=$1" in c.args[0]
            and c.args[1] == "failed"
            and isinstance(c.args[2], str)
            and "auth_wall_detected" in c.args[2]
            and "#login-form" in c.args[2]
        ]
        assert failed_updates, (
            f"Expected a failed status update with auth_wall_detected, "
            f"got {update_calls}"
        )
