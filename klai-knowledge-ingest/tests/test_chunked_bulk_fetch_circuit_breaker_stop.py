"""Onderdeel 2 (2026-08-19, intermedia.com incident #3 — "20 minutes, zero
pages") — the host circuit breaker (knowledge_ingest.host_circuit_breaker)
wired into ``_chunked_bulk_fetch``.

Neither the pre-existing RATE_LIMITED immediate-stop nor the BLOCKED_ANTI_BOT
ratio gate reacts to a site that fails every request for an UNRELATED reason
(plain 5xx, no anti-bot marker) — none of those reason codes trip either
mechanism. These tests lock in the new backstop for exactly that gap, and
the refusal trigger for the "site explicitly refuses us" case.
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import _chunked_bulk_fetch
from knowledge_ingest.reason_codes import FetchReasonCode

# _burst_size_for(0.05) == max(1, min(100, int(0.05 * 10 + 0.5))) == 1 — one
# URL per chunk, so N urls produce N distinct HTTP requests we can fail
# independently and observe the breaker's running tally accumulate.
_ONE_URL_PER_CHUNK_RATE_LIMIT = 0.05


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


def _server_error_page(url: str) -> dict[str, Any]:
    """A plain 5xx with no rate-limit or anti-bot marker at all — the class
    of failure neither existing stop mechanism reacts to."""
    return {
        "url": url,
        "success": False,
        "status_code": 503,
        "error_message": "Service Unavailable",
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _refused_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "success": False,
        "status_code": 403,
        "error_message": "Access Denied",
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _morning_incident_500_error_page(url: str) -> dict[str, Any]:
    """The literal shape from this morning's crawl4ai container log,
    delivered as a page-level result (success=False, HTTP 200 transport)
    rather than a raised exception — classifies RATE_LIMITED via the
    existing (pre-breaker) immediate stop."""
    return {
        "url": url,
        "success": False,
        "status_code": 500,
        "error_message": (
            "Crawl request failed: Blocked by anti-bot protection: HTTP 429 Too Many Requests"
        ),
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _disable_real_pacing_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)


@pytest.mark.asyncio
async def test_five_consecutive_plain_5xx_chunks_stop_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither RATE_LIMITED nor BLOCKED_ANTI_BOT ever appears here — a
    plain 503 with no marker classifies HTTP_5XX, which trips neither
    existing mechanism. Ten URLs, every one 503s: the breaker must stop
    sending after the 5th consecutive failure, leaving 5 URLs never
    attempted."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_server_error_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(10)]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert len(calls) == 5
    assert fetch.stopped_early is True
    assert fetch.circuit_breaker_triggered is True
    assert fetch.circuit_breaker_slowdown_triggered is False
    assert fetch.not_attempted == urls[5:]
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CIRCUIT_BREAKER_STOP.value
    # Give-up semantics, same as a confirmed block — crawl_site must not
    # retry these at a slower pace (see the crawl_site-level test below).
    assert fetch.stop_trigger_reason_code == FetchReasonCode.BLOCKED_ANTI_BOT.value


@pytest.mark.asyncio
async def test_a_success_between_5xx_failures_prevents_the_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 failures, 1 success, 4 more failures — the consecutive-failure
    streak never reaches 5, so every chunk still ships."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []

    def _page_for(url: str, index: int) -> dict[str, Any]:
        return _ok_page(url) if index == 4 else _server_error_page(url)

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        index = len(calls)
        calls.append(list(payload["urls"]))
        return {"results": [_page_for(payload["urls"][0], index)]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(9)]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert len(calls) == 9
    assert fetch.stopped_early is False
    assert fetch.circuit_breaker_triggered is False
    assert fetch.not_attempted == []


@pytest.mark.asyncio
async def test_fifty_percent_failure_ratio_triggers_slowdown_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternating fail/success so the consecutive-streak keeps resetting
    (never reaches 5); the crawl-wide ratio reaches exactly 50% the moment
    10 URLs have been attempted. 50% is NOT above failure_ratio_threshold
    (0.5, 'meer dan de helft'), so this must not abort — but it IS above
    the onderdeel-3 slowdown_ratio_threshold (0.25), so it must trip
    SLOWDOWN, reported to crawl_site as a RATE_LIMITED-flavoured stop so
    the retry-at-a-lower-rate ladder applies instead of giving up.

    Renamed from the pre-onderdeel-3 test of the same scenario, which
    asserted an immediate ABORT at 11 calls — the new lower rung in the
    ladder now intervenes one call earlier, at the 10th, before the ratio
    ever reaches the 11th attempt's 0.545."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []
    # 11 urls: F,S,F,S,F,S,F,S,F,S,F — attempted=10 (after the 10th call,
    # a success) already has 5 of 10 failed (ratio exactly 0.5).
    pattern = [_server_error_page if i % 2 == 0 else _ok_page for i in range(11)]

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        index = len(calls)
        calls.append(list(payload["urls"]))
        return {"results": [pattern[index](payload["urls"][0])]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(15)]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert len(calls) == 10
    assert fetch.stopped_early is True
    # SLOWDOWN is NOT the give-up verdict — circuit_breaker_triggered stays
    # False (it exclusively means "the breaker gave up"); the new, separate
    # flag records the slowdown intervention instead.
    assert fetch.circuit_breaker_triggered is False
    assert fetch.circuit_breaker_slowdown_triggered is True
    assert fetch.not_attempted == urls[10:]
    # RATE_LIMITED, not BLOCKED_ANTI_BOT — crawl_site must retry these at a
    # lower rate, not give up on them.
    assert fetch.stop_trigger_reason_code == FetchReasonCode.RATE_LIMITED.value
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value


@pytest.mark.asyncio
async def test_three_refusals_stop_the_crawl_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three REFUSED observations abort even with a success interleaved —
    the refusal counter is never reset, unlike the consecutive streak."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []
    pattern = [_refused_page, _ok_page, _refused_page, _ok_page, _refused_page]

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        index = len(calls)
        calls.append(list(payload["urls"]))
        return {"results": [pattern[index](payload["urls"][0])]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(10)]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert len(calls) == 5
    assert fetch.stopped_early is True
    assert fetch.circuit_breaker_triggered is True
    assert fetch.circuit_breaker_slowdown_triggered is False
    assert fetch.not_attempted == urls[5:]
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CIRCUIT_BREAKER_STOP.value
    # A refusal never slows down, it gives up — same give-up reason code as
    # a confirmed block, never RATE_LIMITED.
    assert fetch.stop_trigger_reason_code == FetchReasonCode.BLOCKED_ANTI_BOT.value

    # The three real REFUSED observations survive in raw_results, distinct
    # from the abandoned URLs — "how many times did this domain actually
    # refuse us" is not inflated by URLs never sent.
    refused_urls = [
        p["url"]
        for p in fetch.raw_results
        if crawl4ai_client._classify_fetch_outcome(p) == FetchReasonCode.REFUSED.value
    ]
    assert len(refused_urls) == 3


@pytest.mark.asyncio
async def test_morning_incident_simulation_stops_within_a_handful_of_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every block fails with the exact 500-shape from this morning's
    crawl4ai container log — the crawl must break off within the first
    handful of blocks, never hammer through all of them."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_morning_incident_500_error_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://www.intermedia.com/{i}" for i in range(50)]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert len(calls) <= 5, f"expected a handful of blocks, got {len(calls)}"
    assert fetch.stopped_early is True
    assert fetch.not_attempted


@pytest.mark.asyncio
async def test_circuit_breaker_stop_gives_up_immediately_in_crawl_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crawl_site must NOT retry breaker-abandoned URLs at a slower pace
    (Deel B's RATE_LIMITED-only retry path) — it must give up on them the
    same way it already does for a confirmed BLOCKED_ANTI_BOT."""
    _disable_real_pacing_sleep(monkeypatch)

    async def _fake_sitemap(_base: str) -> list[str]:
        return [f"https://example.com/{i}" for i in range(10)]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> crawl4ai_client.CrawlResult:
        return crawl4ai_client.CrawlResult(
            url=start_url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<html></html>",
            word_count=1,
            success=True,
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    calls: list[list[str]] = []

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_server_error_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=20,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    # 5 real 503 observations, then the breaker trips — the remaining
    # sitemap URLs are abandoned as NOT_FETCHED_CIRCUIT_BREAKER_STOP, not
    # silently retried forever (no "crawl_rate_limit_slowdown_retry" loop).
    by_reason: dict[str, int] = {}
    for outcome in outcomes:
        by_reason[outcome["reason_code"]] = by_reason.get(outcome["reason_code"], 0) + 1

    assert by_reason.get(FetchReasonCode.HTTP_5XX.value, 0) == 5
    assert by_reason.get(FetchReasonCode.NOT_FETCHED_CIRCUIT_BREAKER_STOP.value, 0) == 5
    # Only the 5 real 503 attempts were ever sent to the failing batch —
    # crawl_site did not retry the abandoned tail at a slower pace.
    assert sum(len(c) for c in calls) == 5
