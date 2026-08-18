"""Deel B — a rate-limit signal should slow the crawl down, not give up on it.

Yesterday's fix (A1, ``_chunked_bulk_fetch.stopped_early``) correctly stops
the CURRENT chunk-batch the moment a chunk observes RATE_LIMITED /
BLOCKED_ANTI_BOT. It was half the job: ``crawl_site`` never read
``stopped_early`` at all, so the outer while-loop just started the next
batch — of DIFFERENT, still-queued URLs — at the exact same (unreduced)
pace, hammering the site again. Skipped URLs were also finalised as
``not_fetched_rate_limit_stop`` immediately, permanently giving up on them
after a single 429.

This locks in the correct behaviour: RATE_LIMITED lowers the in-job
rate_limit and retries the skipped URLs on a LATER batch (bounded by
``_MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS``, after which giving up is
correct); BLOCKED_ANTI_BOT stops immediately because no rate exists that
fixes a block.
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode


def _rate_limited_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "success": False,
        "status_code": 429,
        "error_message": "Too Many Requests",
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _blocked_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "success": False,
        "status_code": None,
        "error_message": "Blocked by anti-bot protection: JS challenge",
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _ok_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "success": True,
        "status_code": 200,
        "html": "<html><body>Real content, several words here.</body></html>",
        "markdown": "Real content, several words here.",
        "links": {"internal": []},
        "media": {},
    }


def _patch_seed_with_links(monkeypatch: pytest.MonkeyPatch, links: list[str]) -> None:
    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=start_url,
            fit_markdown="Seed",
            raw_markdown="Seed",
            html="<html></html>",
            word_count=2,
            success=True,
            links={"internal": [{"href": h, "text": ""} for h in links]},
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)


def _patch_no_real_slowdown_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def _instant(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(crawl4ai_client, "_slowdown_sleep", _instant)
    return recorded


@pytest.mark.asyncio
async def test_rate_limit_stop_retries_skipped_urls_at_a_lower_rate_on_the_next_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_with_links(
        monkeypatch,
        [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ],
    )
    slowdown_sleeps = _patch_no_real_slowdown_sleep(monkeypatch)

    calls: list[dict[str, Any]] = []

    async def _fake_chunked_bulk_fetch(
        *, urls: list[str], rate_limit: float | None, **_kwargs: Any
    ) -> ChunkedFetchResult:
        calls.append({"urls": list(urls), "rate_limit": rate_limit})
        if len(calls) == 1:
            return ChunkedFetchResult(
                raw_results=[_rate_limited_page(urls[0])],
                not_attempted=urls[1:],
                stopped_early=True,
                stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
            )
        return ChunkedFetchResult(raw_results=[_ok_page(u) for u in urls])

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=2.0,
    )

    assert len(calls) == 2, "the skipped URLs must be retried in a later batch, not abandoned"
    assert calls[0]["urls"] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert calls[0]["rate_limit"] == 2.0
    # The second batch is exactly the URLs skipped by the first — never
    # abandoned, and demonstrably paced slower than the original rate.
    assert calls[1]["urls"] == ["https://example.com/b", "https://example.com/c"]
    assert calls[1]["rate_limit"] < calls[0]["rate_limit"]
    assert calls[1]["rate_limit"] == pytest.approx(1.0)

    # A short, explicit cooldown happened before resuming — not immediate.
    assert slowdown_sleeps == [settings.crawl_rate_limit_slowdown_cooldown_seconds]

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/a"] == FetchReasonCode.RATE_LIMITED.value
    # b and c were RETRIED and succeeded — never permanently marked
    # not-fetched just because the first attempt hit a 429.
    assert by_url["https://example.com/b"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/c"] == FetchReasonCode.SUCCESS.value
    assert "https://example.com/b" not in {
        o["url"]
        for o in outcomes
        if o["reason_code"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
    }
    assert {r.url for r in results} >= {
        "https://example.com/b",
        "https://example.com/c",
    }


@pytest.mark.asyncio
async def test_rate_limit_stop_gives_up_after_max_consecutive_slowdowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site that stays rate-limited through every slowdown attempt must
    eventually be abandoned — slowing down forever is not a fix either."""
    urls = [f"https://example.com/{c}" for c in "abcde"]
    _patch_seed_with_links(monkeypatch, urls)
    slowdown_sleeps = _patch_no_real_slowdown_sleep(monkeypatch)

    calls: list[dict[str, Any]] = []

    async def _fake_chunked_bulk_fetch(
        *, urls: list[str], rate_limit: float | None, **_kwargs: Any
    ) -> ChunkedFetchResult:
        calls.append({"urls": list(urls), "rate_limit": rate_limit})
        return ChunkedFetchResult(
            raw_results=[_rate_limited_page(urls[0])],
            not_attempted=urls[1:],
            stopped_early=True,
            stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
        )

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=20,
        rate_limit=2.0,
    )

    # 1 (initial) + _MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS retries, then give up.
    assert len(calls) == crawl4ai_client._MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS + 1
    rate_limits = [c["rate_limit"] for c in calls]
    assert rate_limits == sorted(rate_limits, reverse=True), "rate must monotonically decrease"
    assert rate_limits[0] == 2.0
    assert rate_limits[-1] < rate_limits[0]
    # One cooldown sleep per successful slowdown decision — not on the
    # final give-up (there is no next batch to wait for).
    assert len(slowdown_sleeps) == crawl4ai_client._MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    # Every URL that was actually the "first in its chunk" got a real,
    # observed RATE_LIMITED outcome.
    for i in range(crawl4ai_client._MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS + 1):
        assert by_url[urls[i]] == FetchReasonCode.RATE_LIMITED.value
    # The last URL, never even attempted after the budget ran out, is
    # honestly marked as a scheduling stop, not a fetch failure.
    assert by_url[urls[-1]] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value


@pytest.mark.asyncio
async def test_blocked_anti_bot_stops_immediately_without_any_slowdown_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLOCKED_ANTI_BOT is not a pacing problem — no rate_limit reduction
    is worth trying, so the crawl must stop after the batch that saw it,
    exactly like before Deel B."""
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    _patch_seed_with_links(monkeypatch, urls)
    slowdown_sleeps = _patch_no_real_slowdown_sleep(monkeypatch)

    calls: list[dict[str, Any]] = []

    async def _fake_chunked_bulk_fetch(
        *, urls: list[str], rate_limit: float | None, **_kwargs: Any
    ) -> ChunkedFetchResult:
        calls.append({"urls": list(urls), "rate_limit": rate_limit})
        return ChunkedFetchResult(
            raw_results=[_blocked_page(urls[0])],
            not_attempted=urls[1:],
            stopped_early=True,
            stop_trigger_reason_code=FetchReasonCode.BLOCKED_ANTI_BOT.value,
        )

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=2.0,
    )

    assert len(calls) == 1, "an anti-bot block must not trigger any slowdown retry"
    assert calls[0]["rate_limit"] == 2.0
    assert slowdown_sleeps == []

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/a"] == FetchReasonCode.BLOCKED_ANTI_BOT.value
    assert by_url["https://example.com/b"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
    assert by_url["https://example.com/c"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
