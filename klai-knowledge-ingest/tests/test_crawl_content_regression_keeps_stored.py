"""A crawl run is judged against what is stored before it writes anything.

A nightly crawl that ran without its login replaced 203 pages with versions
missing 19% of their words, and the site showed no login marker on them. On
production history (2026-11-12) 116 crawl runs replaced at least 5 pages; in
40 of them no replaced page lost over a tenth of its stored words, and only
6 runs had half or more of their replaced pages lose that much: four
logged-out runs and two small ones. So a run in which at least 10 pages
changed and at least half of them lost over a tenth of their stored words
writes nothing, until a person accepts the change.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from knowledge_ingest.adapters.crawler import run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState
from tests.conftest import connection_factory_for

_SITE = "https://docs.example.com"
_GATED_MARKDOWN = (
    "# Setup guide\n\nThe public part of this article.\n\n"
    "[Log in](https://docs.example.com/login?return=/setup) "
    "when you want to read this article."
)


def _text(i: int, kept: int, tail: str = "") -> str:
    words = " ".join(f"term{i:02d}x{w:03d}" for w in range(kept))
    return f"# Page {i}\n\n{words}{tail}"


def _stored_text(i: int) -> str:
    return _text(i, 300)


def _shrunk_text(i: int) -> str:
    # Keeps 240 of the 300 stored words: retained 0.8 and 60 words gone.
    return _text(i, 240)


def _edited_text(i: int) -> str:
    # Keeps 291 stored words and adds two new ones: an ordinary edit.
    return _text(i, 291, " freshly written")


def _page(url: str, markdown: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=markdown,
        raw_markdown=markdown,
        html="<html><body></body></html>",
        word_count=len(markdown.split()),
        success=True,
    )


def _site(changed: int, shrunk: int, unchanged: int = 0) -> tuple[list[CrawlResult], dict]:
    """``changed`` stored pages that differ, the first ``shrunk`` of them losing content."""
    pages: list[CrawlResult] = []
    stored: dict[str, str] = {}
    for i in range(changed + unchanged):
        url = f"{_SITE}/page-{i}"
        stored[url] = _stored_text(i)
        if i >= changed:
            text = _stored_text(i)
        elif i < shrunk:
            text = _shrunk_text(i)
        else:
            text = _edited_text(i)
        pages.append(_page(url, text))
    return pages, stored


async def _run(
    pages: list[CrawlResult], stored: dict[str, str], **job_kwargs
) -> tuple[AsyncMock, AsyncMock, MagicMock, list]:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    outcomes = [
        {"url": p.url, "reason_code": "success", "status_code": 200, "content_length": 1}
        for p in pages
    ]
    ingest = AsyncMock(return_value=True)
    links = AsyncMock(return_value=None)
    with (
        capture_logs() as logs,
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new=AsyncMock(return_value=(pages, outcomes)),
        ),
        patch("knowledge_ingest.adapters.crawler.pg_store.upsert_page_links", new=links),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_active_document_texts",
            new=AsyncMock(return_value=stored),
            create=True,
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
        patch(
            "knowledge_ingest.adapters.crawler._enqueue_taxonomy_backfill_after_crawl",
            new=AsyncMock(return_value=None),
        ),
    ):
        await run_crawl_job(
            connection_factory=connection_factory_for(conn),
            job_id="job-audit",
            org_id="100000000000000002",
            kb_slug="support",
            start_url=f"{_SITE}/",
            connector_id="00000000-0000-0000-0000-00000000c0de",
            **job_kwargs,
        )
    return ingest, links, conn, logs


def _terminal(conn: MagicMock) -> tuple[str | None, dict | None]:
    """Status and summary of the fenced terminal write (finish_crawl_execution)."""
    for call in reversed(conn.execute.await_args_list):
        if "SET status=$1" in call.args[0]:
            status, _error, summary = call.args[1:4]
            return status, json.loads(summary) if summary else None
    return None, None


def _with_links(pages: list[CrawlResult]) -> list[CrawlResult]:
    for page in pages:
        page.links = {"internal": [{"href": f"{_SITE}/page-0", "text": "Home"}]}
    return pages


@pytest.mark.asyncio()
async def test_run_where_most_changed_pages_lost_content_writes_nothing() -> None:
    pages, stored = _site(changed=12, shrunk=7)

    ingest, links, conn, logs = await _run(_with_links(pages), stored)

    assert ingest.await_count == 0
    assert links.await_count == 0
    status, summary = _terminal(conn)
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == "content_regression"
    assert (summary["changed_pages"], summary["pages_lost_content"]) == (12, 7)
    error = [e for e in logs if e["event"] == "crawl_content_regression_stored_pages_kept"]
    assert len(error) == 1
    assert error[0]["log_level"] == "error"
    assert (error[0]["changed_pages"], error[0]["pages_lost_content"]) == (12, 7)
    assert error[0]["median_retained"] == pytest.approx(0.8, abs=0.01)


@pytest.mark.asyncio()
async def test_run_where_a_minority_of_changed_pages_lost_content_writes() -> None:
    pages, stored = _site(changed=12, shrunk=5)

    ingest, _, conn, _ = await _run(pages, stored)

    assert ingest.await_count == 12
    assert _terminal(conn)[0] == "completed"


@pytest.mark.asyncio()
async def test_run_below_the_ten_changed_pages_floor_writes_even_when_all_shrunk() -> None:
    pages, stored = _site(changed=9, shrunk=9, unchanged=20)

    ingest, _, conn, _ = await _run(pages, stored)

    assert ingest.await_count == 29
    assert _terminal(conn)[0] == "completed"


@pytest.mark.asyncio()
async def test_run_where_small_pages_each_lose_a_few_lines_writes() -> None:
    # A documentation site dropped the same few lines from every small page:
    # each loses 15 of 100 words (retained 0.85), under the 50-word floor.
    pages = [_page(f"{_SITE}/page-{i}", _text(i, 85)) for i in range(12)]
    stored = {p.url: _text(i, 100) for i, p in enumerate(pages)}

    ingest, _, conn, _ = await _run(pages, stored)

    assert ingest.await_count == 12
    assert _terminal(conn)[0] == "completed"


@pytest.mark.asyncio()
async def test_accepted_content_regression_writes_and_says_so() -> None:
    pages, stored = _site(changed=12, shrunk=7)

    ingest, _, conn, logs = await _run(pages, stored, accept_content_regression=True)

    assert ingest.await_count == 12
    assert all(c.kwargs["accept_shrink"] is True for c in ingest.await_args_list)
    assert _terminal(conn)[0] == "completed"
    assert [e["event"] for e in logs if e["event"].startswith("crawl_content_regression")] == [
        "crawl_content_regression_accepted"
    ]


@pytest.mark.asyncio()
async def test_healthy_run_with_a_few_edited_pages_writes_every_page() -> None:
    pages, stored = _site(changed=3, shrunk=0, unchanged=30)

    ingest, links, conn, logs = await _run(_with_links(pages), stored)

    assert ingest.await_count == 33
    assert links.await_count == 33
    assert _terminal(conn)[0] == "completed"
    assert not [e for e in logs if e["event"].startswith("crawl_content_regression")]


@pytest.mark.asyncio()
async def test_anonymous_run_walled_at_the_dirty_threshold_writes_nothing() -> None:
    # 3 of 10 pages walled = 0.30, the existing ingest_authwall_dirty_trip_rate.
    pages = [_page(f"{_SITE}/gated-{i}", _GATED_MARKDOWN) for i in range(3)]
    pages += [_page(f"{_SITE}/page-{i}", _stored_text(i)) for i in range(7)]

    ingest, links, conn, _ = await _run(_with_links(pages), {})

    assert ingest.await_count == 0
    assert links.await_count == 0
    status, summary = _terminal(conn)
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == "boilerplate_or_authwall_dominant"


@pytest.mark.asyncio()
async def test_anonymous_run_walled_below_the_dirty_threshold_writes_as_before() -> None:
    pages = [_page(f"{_SITE}/gated-{i}", _GATED_MARKDOWN) for i in range(2)]
    pages += [_page(f"{_SITE}/page-{i}", _stored_text(i)) for i in range(8)]

    ingest, _, _, _ = await _run(pages, {})

    assert ingest.await_count == 10
