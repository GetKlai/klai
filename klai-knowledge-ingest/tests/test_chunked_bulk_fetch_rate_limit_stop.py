"""A1 (bulk-path defects block A) — an observed 429 must stop the
remaining chunks of a ``_chunked_bulk_fetch`` call.

Before this fix ``_chunked_bulk_fetch`` classified nothing inside its own
loop: it POSTed every chunk regardless of what an earlier chunk's response
said, so a site that rate-limited us on chunk 1 got hammered with chunks
2..N anyway. This locks in the opposite: the first RATE_LIMITED (or
BLOCKED_ANTI_BOT) signal — whether a per-URL page result inside an
otherwise-successful chunk, or the chunk's own transport exception — stops
every later chunk from ever being sent.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import _chunked_bulk_fetch
from knowledge_ingest.reason_codes import FetchReasonCode

# _burst_size_for(0.05) == max(1, min(100, int(0.05 * 10 + 0.5))) == 1 — one
# URL per chunk, so three URLs produce three distinct HTTP requests.
_ONE_URL_PER_CHUNK_RATE_LIMIT = 0.05


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


@pytest.mark.asyncio
async def test_per_url_429_in_chunk_one_stops_chunk_two_and_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact scenario from the task: chunk 1 returns a per-URL 429.
    No further HTTP requests may be made, and the skipped URLs must NOT
    be labelled rate_limited (they were never asked)."""
    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_rate_limited_page(payload["urls"][0])]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
    ]

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    # GEEN verdere HTTP-requests: only chunk 1 (the first URL) was ever sent.
    assert calls == [["https://example.com/1"]]

    # De niet-verstuurde URL's krijgen de niet-geprobeerd-code, niet rate_limited.
    assert fetch.stopped_early is True
    assert fetch.not_attempted == ["https://example.com/2", "https://example.com/3"]
    assert fetch.failed == {}

    # The one real observation is still classifiable as RATE_LIMITED from
    # fetch.raw_results — that page dict is untouched.
    assert len(fetch.raw_results) == 1
    assert fetch.raw_results[0]["url"] == "https://example.com/1"


@pytest.mark.asyncio
async def test_single_blocked_anti_bot_signal_no_longer_stops_further_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-18 (antibot-classification-and-threshold): BLOCKED_ANTI_BOT
    used to be RATE_LIMITED's sibling immediate-stop trigger — a single
    signal aborted every remaining chunk. A production audit of 100
    historical BLOCKED_ANTI_BOT outcomes (91 misclassifications) and 60
    crawl jobs (three lost 216/192/17 URLs each to only 4-5 false
    signals) proved that reaction disproportionate. BLOCKED_ANTI_BOT now
    only stops once a crawl-wide ratio threshold is crossed (see
    tests/test_chunked_bulk_fetch_antibot_ratio_stop.py) — RATE_LIMITED
    is unaffected and still stops on the very first signal, since a 429
    is the target site explicitly telling us to back off, not a guess.
    One signal on two URLs (50%) stays well under the default
    ``crawl_antibot_stop_min_count`` floor (3), so chunking continues."""
    # Chunking no longer stops after chunk 1, so a second chunk's pacing
    # gap would otherwise really sleep — same virtual-clock pattern as
    # test_no_stop_when_no_chunk_observes_rate_limit below.
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)

    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        url = payload["urls"][0]
        return {
            "results": [
                {
                    "url": url,
                    "success": False,
                    "status_code": None,
                    "error_message": "Blocked by anti-bot protection: JS challenge",
                    "html": "",
                    "markdown": "",
                    "links": {"internal": []},
                    "media": {},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = ["https://example.com/1", "https://example.com/2"]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert calls == [["https://example.com/1"], ["https://example.com/2"]]
    assert fetch.stopped_early is False
    assert fetch.not_attempted == []


@pytest.mark.asyncio
async def test_chunk_level_transport_429_also_stops_further_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 wrapped as the CHUNK's own transport exception (not a per-URL
    page result) must trigger the same stop."""
    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
        response = httpx.Response(429, json={"detail": "Too Many Requests"}, request=request)
        raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = ["https://example.com/1", "https://example.com/2"]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert calls == [["https://example.com/1"]]
    assert fetch.stopped_early is True
    # The chunk that actually failed keeps its real transport exception —
    # NOT the not-attempted code, that is reserved for chunks never sent.
    assert set(fetch.failed) == {"https://example.com/1"}
    assert fetch.not_attempted == ["https://example.com/2"]


@pytest.mark.asyncio
async def test_no_stop_when_no_chunk_observes_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy multi-chunk fetch is completely unaffected by the new
    stop-check — every chunk still ships."""
    # Real inter-chunk pacing sleeps would otherwise cost real wall-clock
    # time here (three chunks over the small burst size used to force
    # multi-chunk behaviour) — same virtual-clock pattern as
    # test_client_side_pacing.py, this test asserts on chunking, not pacing.
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)

    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_ok_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = ["https://example.com/1", "https://example.com/2", "https://example.com/3"]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert calls == [
        ["https://example.com/1"],
        ["https://example.com/2"],
        ["https://example.com/3"],
    ]
    assert fetch.stopped_early is False
    assert fetch.not_attempted == []
    assert len(fetch.raw_results) == 3


@pytest.mark.asyncio
async def test_observed_rate_limit_still_triggers_domain_rate_limit_lowering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the interaction with
    ``domain_rate_limit_control.count_rate_limit_observations``: a real
    RATE_LIMITED observation must always survive into ``crawl_site``'s
    outcomes and be counted as congestion, however many chunks/retries it
    takes to get there.

    Deel B (2026-08-18, "a stop-signal should slow you down, not give up")
    changed what happens to the URLs skipped by the stop signal: they are
    no longer immediately abandoned as NOT_FETCHED_RATE_LIMIT_STOP — they
    are retried, at a lowered pace, on a later batch. This fake models a
    site that is consistently (but not permanently-unrecoverably) 429ing:
    every URL it actually receives gets a real, observed RATE_LIMITED
    result — so the retry succeeds at OBSERVING every URL, even though
    every one of those observations is itself congestion. See
    ``tests/test_crawl_rate_limit_slowdown.py`` for the dedicated give-up
    (NOT_FETCHED_RATE_LIMIT_STOP after exhausting the retry budget) and
    BLOCKED_ANTI_BOT (stop immediately, no retry) coverage.
    """

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]

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

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        # Every URL crawl4ai actually receives gets its own real 429 —
        # unlike the earlier single-URL-only fake, this must hold across
        # chunk sizes > 1 (Deel B's slowdown floor can raise the burst
        # size relative to this test's deliberately tiny starting
        # rate_limit; see MIN_DOMAIN_RATE_LIMIT).
        return {"results": [_rate_limited_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/1"] == FetchReasonCode.RATE_LIMITED.value
    assert by_url["https://example.com/2"] == FetchReasonCode.RATE_LIMITED.value
    assert by_url["https://example.com/3"] == FetchReasonCode.RATE_LIMITED.value
    assert FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value not in by_url.values()

    # The congestion signal counted by domain_rate_limit_control fires on
    # RATE_LIMITED / BLOCKED_ANTI_BOT — confirm every real observation is
    # present and would still trip it. The seed page (fetched separately,
    # SUCCESS) is the only clean observation.
    from knowledge_ingest.domain_rate_limit_control import count_rate_limit_observations

    observation = count_rate_limit_observations(outcomes)
    assert observation.congestion_count == 3
    assert observation.clean_count == 1  # the seed page only
