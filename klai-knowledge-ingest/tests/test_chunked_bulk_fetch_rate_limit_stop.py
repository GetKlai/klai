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
async def test_blocked_anti_bot_signal_also_stops_further_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLOCKED_ANTI_BOT is the sibling trigger to RATE_LIMITED — both mean
    "stop asking this site right now"."""
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

    assert calls == [["https://example.com/1"]]
    assert fetch.not_attempted == ["https://example.com/2"]


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
    ``domain_rate_limit_control.count_rate_limit_observations``: even
    though only ONE chunk's URL was actually rate-limited, that single real
    observation must still appear in ``crawl_site``'s outcomes as
    RATE_LIMITED — the NOT_FETCHED_RATE_LIMIT_STOP entries for the skipped
    URLs must not silently replace it."""

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
        return {"results": [_rate_limited_page(payload["urls"][0])]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/1"] == FetchReasonCode.RATE_LIMITED.value
    assert by_url["https://example.com/2"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
    assert by_url["https://example.com/3"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value

    # The congestion signal counted by domain_rate_limit_control fires on
    # RATE_LIMITED / BLOCKED_ANTI_BOT — confirm the one real observation is
    # present and would still trip it. The seed page (fetched separately,
    # SUCCESS) is the only clean observation; the two
    # NOT_FETCHED_RATE_LIMIT_STOP skips must NOT count as additional clean
    # or congestion signals.
    from knowledge_ingest.domain_rate_limit_control import count_rate_limit_observations

    observation = count_rate_limit_observations(outcomes)
    assert observation.had_congestion is True
    assert observation.clean_count == 1  # the seed page only
