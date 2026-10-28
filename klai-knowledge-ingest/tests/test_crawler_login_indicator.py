"""Tests for the login-indicator auth-wall guard (SPEC-CRAWLER-004 Fase B).

Covers REQ-02.3 and AC-02.3:
- When ``login_indicator_selector`` is set and crawl4ai flags a page as
  ``success=False``, ``run_crawl_job`` halts the BFS and writes exactly
  one ``crawl_jobs`` row with ``status='failed'`` and
  ``error='auth_wall_detected: {selector}'``.
- No follow-up pages are ingested after the first auth-walled page.
- The indicator is matched against the fetched HTML (crawl4ai 0.9.5 refuses
  the JS wait_for that used to carry it).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from knowledge_ingest.adapters.crawler import (
    AnonymousAuthWallDetected,
    AuthWallDetected,
    _ingest_crawl_result,
    run_crawl_job,
)
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.crawl_result_processing import html_has_element
from knowledge_ingest.utils.auth_wall_detector import AuthWallSignal
from tests.conftest import connection_factory_for


class _StopAfterCheck(Exception):
    pass


class TestIngestCrawlResultAuthWall:
    """_ingest_crawl_result raises AuthWallDetected when appropriate."""

    @pytest.mark.asyncio()
    async def test_login_indicator_in_fetched_html_raises_auth_wall(self) -> None:
        walled = CrawlResult(
            url="https://wiki.example/page",
            fit_markdown="Log in to continue",
            raw_markdown="Log in to continue",
            html='<html><body><form id="login-form" class="a b"></form></body></html>',
            word_count=4,
            success=True,
        )
        mock_conn = MagicMock()
        with pytest.raises(AuthWallDetected) as excinfo:
            await _ingest_crawl_result(
                mock_conn,
                walled,
                url=walled.url,
                org_id="org",
                kb_slug="support",
                login_indicator_selector="#login-form",
            )
        assert "auth_wall_detected" in str(excinfo.value)
        assert excinfo.value.selector == "#login-form"

    @pytest.mark.asyncio()
    async def test_an_indicator_in_the_stripped_header_is_not_a_login_wall(self) -> None:
        """The indicator is matched on crawl4ai's cleaned HTML (after excluded_tags
        and excluded_selector), as the page-side check ran after the same chrome
        removal; a user menu that only lives in the header never ends a crawl."""
        logged_in = CrawlResult(
            url="https://wiki.example/page",
            fit_markdown="Article body with enough words to be a page.",
            raw_markdown="Article body with enough words to be a page.",
            html=(
                '<html><body><header><div class="user-menu">me</div></header>'
                "<main>Article</main></body></html>"
            ),
            cleaned_html="<html><body><main>Article</main></body></html>",
            word_count=9,
            success=True,
        )
        seen: list[str] = []

        def matcher(html: str, selector: str) -> bool:
            seen.append(html)
            raise _StopAfterCheck

        with (
            patch("knowledge_ingest.adapters.crawler.html_has_element", side_effect=matcher),
            pytest.raises(_StopAfterCheck),
        ):
            await _ingest_crawl_result(
                MagicMock(),
                logged_in,
                url=logged_in.url,
                org_id="org",
                kb_slug="support",
                login_indicator_selector=".user-menu",
            )
        assert seen == [logged_in.cleaned_html]
        assert html_has_element(logged_in.html, ".user-menu") is True
        assert html_has_element(logged_in.cleaned_html, ".user-menu") is False

    @pytest.mark.asyncio()
    async def test_failed_result_raises_generic_even_with_selector(self) -> None:
        failed = CrawlResult(
            url="https://example/page",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="network timeout",
        )
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="Crawl failed"):
            await _ingest_crawl_result(
                mock_conn,
                failed,
                url=failed.url,
                org_id="org",
                kb_slug="support",
                login_indicator_selector="#login-form",
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
            fit_markdown="Log in",
            raw_markdown="Log in",
            html='<html><body><form id="login-form"></form></body></html>',
            word_count=2,
            success=True,
        )
        never_reached = CrawlResult(
            url="https://wiki.example/c",
            fit_markdown="content",
            raw_markdown="content",
            html="<html></html>",
            word_count=10,
            success=True,
        )

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        # SPEC-INGEST-RECONCILE-001 AC-4: crawl_site returns
        # ``(results, fetch_outcomes)``. Synthesise the outcomes that
        # would correspond to these CrawlResults.
        _login_outcomes = [
            {
                "url": happy.url,
                "reason_code": "success",
                "status_code": 200,
                "content_length": len(happy.html),
            },
            {
                "url": walled.url,
                "reason_code": "auth_error",
                "status_code": 401,
                "content_length": 0,
            },
            {
                "url": never_reached.url,
                "reason_code": "success",
                "status_code": 200,
                "content_length": len(never_reached.html),
            },
        ]
        with (
            patch(
                "knowledge_ingest.adapters.crawler.crawl_site",
                new=AsyncMock(return_value=([happy, walled, never_reached], _login_outcomes)),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "knowledge_ingest.adapters.crawler._ingest_crawl_result",
                new=AsyncMock(side_effect=[None, AuthWallDetected("#login-form")]),
            ) as ingest_mock,
        ):
            await run_crawl_job(
                connection_factory=connection_factory_for(mock_conn),
                job_id="job-1",
                org_id="org",
                kb_slug="support",
                start_url="https://wiki.example",
                login_indicator_selector="#login-form",
            )

        # The walled page raises, so the third page is never attempted.
        assert ingest_mock.call_count == 2
        # SPEC-TI-003-FOLLOWUP-001: _ingest_crawl_result(conn, result, url, org, kb, ...)
        assert [c.args[1] for c in ingest_mock.await_args_list] == [happy, walled]

        # Verify at least one UPDATE marked the job as failed with auth_wall_detected.
        update_calls = mock_conn.execute.await_args_list
        failed_updates = [
            c
            for c in update_calls
            if len(c.args) >= 4
            and isinstance(c.args[0], str)
            and "UPDATE knowledge.crawl_jobs" in c.args[0]
            and "status=$1" in c.args[0]
            and c.args[1] == "failed"
            and isinstance(c.args[2], str)
            and "auth_wall_detected" in c.args[2]
            and "#login-form" in c.args[2]
        ]
        assert failed_updates, (
            f"Expected a failed status update with auth_wall_detected, got {update_calls}"
        )

    @pytest.mark.asyncio()
    async def test_saved_cookies_with_login_wall_fail_loudly(self) -> None:
        walled = CrawlResult(
            url="https://wiki.example/private",
            fit_markdown="[Log in](https://wiki.example/login) to read this article",
            raw_markdown="[Log in](https://wiki.example/login) to read this article",
            html="<html></html>",
            word_count=8,
            success=True,
        )
        outcomes = [
            {
                "url": walled.url,
                "reason_code": "success",
                "status_code": 200,
                "content_length": len(walled.html or ""),
            },
            {
                "url": "https://wiki.example/omitted",
                "reason_code": "not_fetched_budget_exhausted",
                "status_code": None,
                "content_length": 0,
            },
        ]

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        wall_exc = AnonymousAuthWallDetected(
            walled.url,
            AuthWallSignal(
                pattern="auth_wall_classifier",
                evidence=("embedded_login_gate",),
                confidence=0.95,
            ),
        )
        with (
            patch(
                "knowledge_ingest.adapters.crawler.crawl_site",
                new=AsyncMock(return_value=([walled], outcomes)),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "knowledge_ingest.adapters.crawler._ingest_crawl_result",
                new=AsyncMock(side_effect=wall_exc),
            ) as ingest_mock,
            structlog.testing.capture_logs() as captured,
        ):
            await run_crawl_job(
                connection_factory=connection_factory_for(mock_conn),
                job_id="job-1",
                org_id="org",
                kb_slug="support",
                start_url="https://wiki.example",
                cookies=[{"name": "session", "value": "dummy"}],
            )

        # One walled page out of one fetched: an authenticated crawl losing
        # its session must page someone, not only end as failed_partial.
        rate_events = [e for e in captured if e["event"] == "crawl_job_auth_wall_rate_high"]
        assert [(e["log_level"], e["wall_rate"]) for e in rate_events] == [("error", 1.0)]

        ingest_mock.assert_awaited_with(
            mock_conn,
            walled,
            walled.url,
            "org",
            "support",
            stored=None,
            login_indicator_selector=None,
            authenticated_context=True,
            connector_id=None,
            resource_key=None,
        )

        terminal_updates = [
            c
            for c in mock_conn.execute.await_args_list
            if len(c.args) >= 3
            and isinstance(c.args[0], str)
            and "UPDATE knowledge.crawl_jobs" in c.args[0]
            and "status=$1" in c.args[0]
            and c.args[1] == "failed_partial"
            and isinstance(c.args[3], str)
        ]
        assert terminal_updates
        summary = terminal_updates[-1].args[3]
        assert '"reason": "auth_wall_detected"' in summary
        assert '"reason": "crawl_budget_exhausted"' in summary
        assert "Refresh the saved cookies" in summary
