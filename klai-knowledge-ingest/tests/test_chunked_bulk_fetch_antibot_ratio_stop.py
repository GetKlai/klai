"""2026-08-18 (antibot-classification-and-threshold) — BLOCKED_ANTI_BOT must
stop ``_chunked_bulk_fetch`` on a crawl-wide RATIO, not on the first signal.

Production evidence that motivated this fix:

- Every historical ``blocked_anti_bot`` outcome (100 rows) broken down by
  status code showed 91 were noise (see
  ``TestClassifyFetchOutcomeStructuralGuessVsStatusCode`` in
  ``test_crawl_site_reconcile.py`` for the wijziging-1 half of this fix).
- 60 crawl jobs broken down by anti-bot signal share: 48 had zero signals,
  7 stayed under 2%, 5 sat between 2% and 10%, and ZERO landed between 10%
  and 100% — the worst observed noise ratio across all 60 jobs was 5.9%.
- Three real jobs paid for the old single-signal stop directly: 216, 192,
  and 17 URLs never even attempted, caused by only 4, 4, and 5 signals
  respectively.

``RATE_LIMITED`` is deliberately NOT touched by this fix — a 429 is the
target site explicitly telling us to slow down, a fundamentally different,
reliable signal from a heuristic anti-bot guess. It still stops
``_chunked_bulk_fetch`` on the very first observation (see
``test_chunked_bulk_fetch_rate_limit_stop.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import _chunked_bulk_fetch

# Same derivation as test_chunked_bulk_fetch_rate_limit_stop.py:
# _burst_size_for(0.05) == max(1, min(100, int(0.05 * 10 + 0.5))) == 1 — one
# URL per chunk, so N urls produce N distinct HTTP requests we can fail
# independently and observe the running crawl-wide tally accumulate.
_ONE_URL_PER_CHUNK_RATE_LIMIT = 0.05

_ANTIBOT_MESSAGE = "Blocked by anti-bot protection: Cloudflare JS challenge"


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


def _antibot_page(url: str) -> dict[str, Any]:
    # 403 + a concrete Tier-1 vendor signature — classifies BLOCKED_ANTI_BOT
    # regardless of the wijziging-1 status-code fix, so this test file
    # exercises the wijziging-2 threshold logic in isolation.
    return {
        "url": url,
        "success": False,
        "status_code": 403,
        "error_message": _ANTIBOT_MESSAGE,
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


def _disable_real_pacing_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same virtual-clock pattern as the rate-limit-stop test module: a
    multi-chunk fetch with ``rate_limit`` set would otherwise really sleep
    between chunks."""
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)


@pytest.mark.asyncio
async def test_four_antibot_signals_on_400_plus_pages_does_not_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact storyline from production: four misclassified anti-bot
    signals scattered across a 460-page crawl (well under the default 25%
    ratio / 3-signal floor) must NOT abort the remaining pages — this is
    precisely the failure mode that lost 216/192/17 real pages."""
    urls = [f"https://example.com/page-{i}" for i in range(460)]
    antibot_urls = {
        "https://example.com/page-50",
        "https://example.com/page-150",
        "https://example.com/page-250",
        "https://example.com/page-350",
    }

    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        chunk_urls = payload["urls"]
        calls.append(list(chunk_urls))
        pages = [_antibot_page(u) if u in antibot_urls else _ok_page(u) for u in chunk_urls]
        return {"results": pages}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    # rate_limit=None -> fixed _BULK_CHUNK_SIZE (100) chunking, no pacing
    # sleep is ever invoked (see _chunked_bulk_fetch: the inter-chunk sleep
    # is gated on `rate_limit is not None`).
    fetch = await _chunked_bulk_fetch(urls=urls, crawler_config={}, cookies=None)

    # Every URL was actually requested — nothing skipped.
    assert sum(len(c) for c in calls) == 460
    assert fetch.stopped_early is False
    assert fetch.not_attempted == []
    assert len(fetch.raw_results) == 460


@pytest.mark.asyncio
async def test_majority_blocked_crawl_stops_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crawl where the anti-bot ratio genuinely crosses both gates (ratio
    >= 25% AND count >= 3) must still stop — the fix narrows WHEN we stop,
    it does not disable stopping. Also demonstrates the ratio is tracked
    CUMULATIVELY across the whole crawl, not per current (1-URL) chunk:
    the 7th URL alone is 100% anti-bot for its own chunk, yet the crawl
    keeps going because the crawl-wide count (2) is still under the floor
    (3); only the 8th URL's cumulative tally (3 signals / 8 attempted =
    37.5%) crosses both thresholds."""
    _disable_real_pacing_sleep(monkeypatch)

    # 5 clean, then 3 anti-bot in a row, then 2 URLs that must never be sent.
    urls = [f"https://example.com/{i}" for i in range(1, 11)]
    antibot_urls = {urls[5], urls[6], urls[7]}  # index 6, 7, 8 (1-based /6 /7 /8)

    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = payload["urls"][0]
        calls.append([url])
        page = _antibot_page(url) if url in antibot_urls else _ok_page(url)
        return {"results": [page]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    # Only the first 8 URLs were ever requested (5 clean + 3 anti-bot); the
    # crawl-wide ratio only crosses the floor+ratio gate after the 8th.
    assert calls == [[u] for u in urls[:8]]
    assert fetch.stopped_early is True
    assert fetch.not_attempted == urls[8:]
    assert len(fetch.raw_results) == 8


@pytest.mark.asyncio
async def test_small_crawl_below_absolute_floor_does_not_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's specified edge case: a 3-page crawl where 1 page signals
    anti-bot is already 33% — well above the 25% ratio gate — yet must NOT
    stop, because the absolute floor (default 3) protects small crawls
    from a single stray signal."""
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        pages = [
            _antibot_page(u) if u == "https://example.com/b" else _ok_page(u)
            for u in payload["urls"]
        ]
        return {"results": pages}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    # rate_limit=None -> all 3 URLs fit in a single 100-URL chunk, so the
    # 33% ratio is computed over the whole crawl in one pass.
    fetch = await _chunked_bulk_fetch(urls=urls, crawler_config={}, cookies=None)

    assert fetch.stopped_early is False
    assert fetch.not_attempted == []
    assert len(fetch.raw_results) == 3


@pytest.mark.asyncio
async def test_thresholds_are_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ratio and floor must be configurable, matching the existing
    crawl_* settings pattern (config.py) — not hardcoded constants."""
    monkeypatch.setattr(settings, "crawl_antibot_stop_ratio", 0.5)
    monkeypatch.setattr(settings, "crawl_antibot_stop_min_count", 1)
    _disable_real_pacing_sleep(monkeypatch)

    urls = ["https://example.com/1", "https://example.com/2"]

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = payload["urls"][0]
        return {"results": [_antibot_page(url)]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    # With min_count=1 and ratio=0.5, the very first signal (1/1 = 100%)
    # already crosses both lowered gates.
    assert fetch.stopped_early is True
    assert fetch.not_attempted == ["https://example.com/2"]
