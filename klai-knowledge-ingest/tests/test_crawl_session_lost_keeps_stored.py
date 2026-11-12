"""A crawl whose saved session is gone replaces no stored page.

When a site's login lapses, pages with a protected section render a login
gate, while their siblings render a shortened public version with no gate
at all. Per-page wall detection skipped only the gated pages, so the
shortened siblings were ingested over the full versions captured while the
session was valid. Production measurement on 2026-11-12: every page a
logged-out night rewrote lost content against its logged-in version, with
a median loss of 18.5%. A run that sees the session is gone therefore
writes nothing, so the knowledge base keeps what was last read logged in.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState
from tests.conftest import connection_factory_for

_GATED_MARKDOWN = (
    "# Setup guide\n\nThe public part of this article.\n\n"
    "[Log in](https://wiki.example.com/login?return=/setup) "
    "when you want to read this article."
)
_TEASER_MARKDOWN = "# Feature overview\n\nThe public part of this article, cut short by the site."


def _page(url: str, markdown: str, html: str = "<html><body></body></html>") -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=markdown,
        raw_markdown=markdown,
        html=html,
        word_count=len(markdown.split()),
        success=True,
    )


async def _run(pages: list[CrawlResult], **job_kwargs) -> tuple[AsyncMock, MagicMock]:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    outcomes = [
        {"url": p.url, "reason_code": "success", "status_code": 200, "content_length": 1}
        for p in pages
    ]
    ingest = AsyncMock(return_value=True)
    with (
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new=AsyncMock(return_value=(pages, outcomes)),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.upsert_page_links",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch("knowledge_ingest.adapters.crawler._ingest_crawl_result", new=ingest),
        patch(
            "knowledge_ingest.adapters.crawler.get_domain_rate_limit_state",
            new=AsyncMock(
                return_value=DomainRateLimitState(
                    rate_limit=None, clean_streak=0, last_congestion_at=None
                )
            ),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.save_domain_rate_limit_state",
            new=AsyncMock(return_value=None),
        ),
    ):
        await run_crawl_job(
            connection_factory=connection_factory_for(conn),
            job_id="job-session-lost",
            org_id="100000000000000002",
            kb_slug="support",
            start_url="https://wiki.example.com/nl/",
            connector_id="00000000-0000-0000-0000-00000000c0de",
            **job_kwargs,
        )
    return ingest, conn


def _terminal_summary(conn: MagicMock) -> dict | None:
    for call in reversed(conn.execute.await_args_list):
        for arg in call.args:
            if isinstance(arg, str) and arg.startswith("{"):
                return json.loads(arg)
    return None


@pytest.mark.asyncio()
async def test_authenticated_crawl_that_hits_a_login_gate_replaces_no_stored_page() -> None:
    gated = _page("https://wiki.example.com/nl/setup", _GATED_MARKDOWN)
    teaser = _page("https://wiki.example.com/nl/features", _TEASER_MARKDOWN)

    ingest, conn = await _run(
        [teaser, gated],
        cookies=[{"name": "session", "value": "synthetic", "domain": "wiki.example.com"}],
    )

    assert ingest.await_count == 0
    summary = _terminal_summary(conn)
    assert summary is not None
    assert summary["reason"] == "auth_wall_detected"
    assert summary["sample_urls"] == ["https://wiki.example.com/nl/setup"]


@pytest.mark.asyncio()
async def test_login_indicator_on_any_page_replaces_no_stored_page() -> None:
    teaser = _page("https://wiki.example.com/nl/features", _TEASER_MARKDOWN)
    login_page = _page(
        "https://wiki.example.com/nl/setup",
        "# Sign in",
        html='<html><body><form id="login-form"></form></body></html>',
    )

    ingest, _ = await _run([teaser, login_page], login_indicator_selector="#login-form")

    assert ingest.await_count == 0
