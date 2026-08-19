"""Onderdeel 3 (2026-08-19, "close the gap between nothing and giving up")
— crawl_site-level integration tests for the host circuit breaker's new
SLOWDOWN verdict (knowledge_ingest.host_circuit_breaker.BreakerVerdict.
SLOWDOWN).

Before this fix: a crawl with a ~30% failure rate for a reason
``_chunked_bulk_fetch`` cannot read as RATE_LIMITED (crawl4ai wrapping a
429 in an opaque 500 is the incident that motivated this — see
host_circuit_breaker.py's module docstring) never slowed down (no
readable RATE_LIMITED signal) and never gave up (below the 50% abort
ratio) — it kept hammering the site at full speed for the rest of the
crawl's page budget.

These tests exercise the wiring end-to-end at the ``crawl_site`` level,
the same way ``tests/test_crawl_rate_limit_slowdown.py`` locks in the
pre-existing (real-429) Deel B retry/give-up ladder — by mocking
``_chunked_bulk_fetch`` directly and controlling exactly which
``ChunkedFetchResult`` flags it returns, so these tests do not depend on
choreographing crawl4ai's bulk-fetch/chunking internals to reproduce a
specific failure ratio.

Pure-function coverage of the ratio ladder itself (SLOWDOWN vs CONTINUE
vs the two ABORT verdicts) lives in tests/test_host_circuit_breaker.py.
Wiring from a real failure pattern into a single ``_chunked_bulk_fetch``
call's ``ChunkedFetchResult`` flags lives in
tests/test_chunked_bulk_fetch_circuit_breaker_stop.py.
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
async def test_circuit_breaker_slowdown_retries_skipped_urls_at_a_lower_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A breaker SLOWDOWN verdict (an unreadable-cause high failure ratio,
    e.g. crawl4ai wrapping a 429 in an opaque 500) must retry the abandoned
    URLs on a later batch at a halved rate — the same treatment a genuine,
    readable RATE_LIMITED signal already gets (Deel B) — not give up."""
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
            # First batch: the circuit breaker's ratio ladder tripped
            # SLOWDOWN on an opaque-cause failure — no genuine 429 was ever
            # observed, only a high failure rate.
            return ChunkedFetchResult(
                raw_results=[_rate_limited_page(urls[0])],
                not_attempted=urls[1:],
                stopped_early=True,
                stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
                circuit_breaker_slowdown_triggered=True,
            )
        return ChunkedFetchResult(raw_results=[_ok_page(u) for u in urls])

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=2.0,
    )

    assert len(calls) == 2, "the abandoned URLs must be retried in a later batch, not given up on"
    assert calls[1]["urls"] == ["https://example.com/b", "https://example.com/c"]
    # Same floor/halving mechanism as the real-429 path — see
    # _lower_rate_limit_for_slowdown.
    assert calls[1]["rate_limit"] < calls[0]["rate_limit"]
    assert calls[1]["rate_limit"] == pytest.approx(1.0)
    assert slowdown_sleeps == [settings.crawl_rate_limit_slowdown_cooldown_seconds]

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/b"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/c"] == FetchReasonCode.SUCCESS.value
    assert {r.url for r in results} >= {
        "https://example.com/b",
        "https://example.com/c",
    }


@pytest.mark.asyncio
async def test_circuit_breaker_slowdown_gives_up_after_max_consecutive_halvings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site whose failure ratio stays high through every slowdown attempt
    must eventually be abandoned too — slowing down forever is not a fix,
    the same conclusion the real-429 path already reaches via
    _MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS. This is the SAME cap, reused —
    no second, breaker-specific give-up counter exists."""
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
            circuit_breaker_slowdown_triggered=True,
        )

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=20,
        rate_limit=2.0,
    )

    # 1 (initial) + _MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS retries, then give up
    # — identical shape to the real-429 exhaustion test.
    assert len(calls) == crawl4ai_client._MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS + 1
    rate_limits = [c["rate_limit"] for c in calls]
    assert rate_limits == sorted(rate_limits, reverse=True), "rate must monotonically decrease"
    assert rate_limits[-1] < rate_limits[0]
    assert len(slowdown_sleeps) == crawl4ai_client._MAX_CONSECUTIVE_RATE_LIMIT_SLOWDOWNS

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    # The final abandoned URL is honestly marked as a rate-limit-flavoured
    # scheduling stop — NOT_FETCHED_CIRCUIT_BREAKER_STOP is reserved for the
    # give-up (ABORT) verdicts, not for an exhausted SLOWDOWN ladder.
    assert by_url[urls[-1]] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value


@pytest.mark.asyncio
async def test_a_batch_flagged_by_both_real_rate_limit_and_breaker_slowdown_halves_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single ``_chunked_bulk_fetch`` call can only ever produce ONE
    ``ChunkedFetchResult`` (the chunking loop breaks at most once — see
    that function's docstring), so a batch where BOTH the pre-existing
    genuine-RATE_LIMITED signal AND the new breaker SLOWDOWN verdict would
    apply must still result in exactly ONE halving in crawl_site, not two.
    This is the mechanical reason double-slowdown cannot happen: there is
    only one stop_trigger_reason_code / not_attempted per call for
    crawl_site to react to, regardless of how many trigger sources
    contributed to that single stop."""
    _patch_seed_with_links(
        monkeypatch,
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
    )
    slowdown_sleeps = _patch_no_real_slowdown_sleep(monkeypatch)

    calls: list[dict[str, Any]] = []

    async def _fake_chunked_bulk_fetch(
        *, urls: list[str], rate_limit: float | None, **_kwargs: Any
    ) -> ChunkedFetchResult:
        calls.append({"urls": list(urls), "rate_limit": rate_limit})
        if len(calls) == 1:
            # Simulates a chunk that is simultaneously a genuine 429 AND
            # crossed the breaker's slowdown ratio in the same evaluation —
            # ChunkedFetchResult only ever carries ONE stop signal.
            return ChunkedFetchResult(
                raw_results=[_rate_limited_page(urls[0])],
                not_attempted=urls[1:],
                stopped_early=True,
                stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
                circuit_breaker_slowdown_triggered=True,
            )
        return ChunkedFetchResult(raw_results=[_ok_page(u) for u in urls])

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=2.0,
    )

    assert len(calls) == 2
    # Halved exactly ONCE: 2.0 -> 1.0, not 2.0 -> 0.5 (which would be two
    # halvings collapsed into a single batch transition).
    assert calls[0]["rate_limit"] == 2.0
    assert calls[1]["rate_limit"] == pytest.approx(1.0)
    # Exactly one cooldown sleep for the one halving that happened.
    assert slowdown_sleeps == [settings.crawl_rate_limit_slowdown_cooldown_seconds]
