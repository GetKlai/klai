"""Client-side crawl pacing: burst-size derivation and inter-chunk gaps.

crawl4ai's REST server builds its own ``MemoryAdaptiveDispatcher`` and
ignores the ``mean_delay`` / ``semaphore_count`` we set on
``CrawlerRunConfig`` (measured live: 8 URLs with ``mean_delay=2.0``
finished in 3.2s instead of the predicted 16s). A single bulk request is
one burst on the target site (16 URLs completed within 330ms of each
other). Real rate limiting can therefore only happen client-side, via two
knobs: how many URLs go in one request (the burst) and how long we wait
between requests (the gap). This module tests both knobs in isolation,
with no network calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import _burst_size_for, _chunked_bulk_fetch

# ---------------------------------------------------------------------------
# A. _burst_size_for — pure function, no mocking needed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate_limit", "expected_burst"),
    [
        (None, 100),
        (2.0, 20),
        (1.0, 10),
        (0.5, 5),
        (0.25, 3),
        # Bankers-rounding regression guard: round(0.5) == 0 and
        # round(2.5) == 2 in Python. int(x + 0.5) must NOT collapse these.
        (0.05, 1),  # 0.05 * 10 + 0.5 = 1.0 -> int() = 1, never 0
    ],
)
def test_burst_size_for_expected_values(rate_limit: float | None, expected_burst: int) -> None:
    assert _burst_size_for(rate_limit) == expected_burst


def test_burst_size_for_never_exceeds_bulk_chunk_size() -> None:
    """crawl4ai's server 422s on more than 100 URLs per request regardless
    of how permissive rate_limit is."""
    assert _burst_size_for(1000.0) == crawl4ai_client._BULK_CHUNK_SIZE


def test_burst_size_for_never_drops_below_one() -> None:
    assert _burst_size_for(0.0001) == 1


# ---------------------------------------------------------------------------
# Test doubles for _chunked_bulk_fetch's pacing clock/sleep, mirroring the
# existing _recovery_monotonic / _recovery_sleep indirection pattern so the
# suite never sleeps for real.
# ---------------------------------------------------------------------------


class _VirtualClock:
    """A monotonic clock driven entirely by an in-process 'sleep'.

    ``_pacing_sleep`` advances the clock by the requested duration instead
    of actually waiting, so a test asserting on effective throughput runs
    in milliseconds regardless of the rate_limit being simulated.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Simulate wall-clock time consumed by work (e.g. a chunk fetch)."""
        self.now += seconds


def _install_virtual_clock(monkeypatch: pytest.MonkeyPatch) -> _VirtualClock:
    clock = _VirtualClock()
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", clock.monotonic)
    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", clock.sleep)
    return clock


def _fake_crawl_sync_factory(clock: _VirtualClock, *, chunk_duration: float = 0.0) -> Any:
    """Build a ``_crawl_sync`` stand-in that returns one result per URL and
    advances the virtual clock by ``chunk_duration`` to simulate the wall-
    clock cost of the (mocked) network round-trip.
    """

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        urls = payload["urls"]
        clock.advance(chunk_duration)
        return {
            "results": [
                {"url": u, "success": True, "markdown": "content", "links": {"internal": []}}
                for u in urls
            ]
        }

    return _fake_crawl_sync


# ---------------------------------------------------------------------------
# B. Measured cadence — the property that makes this approach client-side
# rate limiting rather than a cosmetic delay.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rate_limit", [2.0, 0.5])
async def test_chunked_bulk_fetch_honours_rate_limit_cadence(
    monkeypatch: pytest.MonkeyPatch, rate_limit: float
) -> None:
    """Effective throughput (urls / virtual elapsed seconds) must equal the
    requested rate_limit, within a small margin — this is what makes the
    pacing real instead of a fixed sleep."""
    clock = _install_virtual_clock(monkeypatch)
    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync_factory(clock))

    burst = _burst_size_for(rate_limit)
    # Enough chunks that the fencepost effect (N chunks only have N-1 gaps
    # between them, so a handful of chunks measures slightly above nominal)
    # washes out within the margin below.
    num_urls = burst * 50
    urls = [f"https://example.com/p{i}" for i in range(num_urls)]

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=rate_limit,
    )

    assert fetch.failed == {}
    assert len(fetch.raw_results) == num_urls

    elapsed = clock.now
    assert elapsed > 0
    effective_rate = num_urls / elapsed
    assert effective_rate == pytest.approx(rate_limit, rel=0.05)


# ---------------------------------------------------------------------------
# C. Chunk duration is credited against the gap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_chunk_is_not_further_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a chunk's own (simulated) network round-trip already took longer
    than the required gap, no additional sleep must be inserted."""
    clock = _install_virtual_clock(monkeypatch)
    rate_limit = 1.0
    burst = _burst_size_for(rate_limit)  # 10 -> gap = 10s
    gap = burst / rate_limit
    # Chunk itself "takes" longer than the gap.
    monkeypatch.setattr(
        crawl4ai_client,
        "_crawl_sync",
        _fake_crawl_sync_factory(clock, chunk_duration=gap * 2),
    )

    urls = [f"https://example.com/p{i}" for i in range(burst * 2)]

    await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=rate_limit,
    )

    assert clock.sleeps == [], "chunk already exceeded the gap; no sleep may be added"


@pytest.mark.asyncio
async def test_fast_chunk_sleeps_exactly_the_remaining_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a chunk finishes well within the gap, the sleep must make up
    exactly the difference — not the full gap again."""
    clock = _install_virtual_clock(monkeypatch)
    rate_limit = 1.0
    burst = _burst_size_for(rate_limit)  # 10 -> gap = 10s
    gap = burst / rate_limit
    chunk_duration = 2.0
    monkeypatch.setattr(
        crawl4ai_client,
        "_crawl_sync",
        _fake_crawl_sync_factory(clock, chunk_duration=chunk_duration),
    )

    urls = [f"https://example.com/p{i}" for i in range(burst * 2)]

    await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=rate_limit,
    )

    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(gap - chunk_duration)


# ---------------------------------------------------------------------------
# D. rate_limit=None must be a complete no-op: identical chunking and zero
# sleeps, matching current (pre-pacing) behaviour exactly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_rate_limit_means_no_behaviour_change(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _install_virtual_clock(monkeypatch)
    calls: list[int] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(len(payload["urls"]))
        return {
            "results": [
                {
                    "url": u,
                    "success": True,
                    "markdown": "content",
                    "links": {"internal": []},
                }
                for u in payload["urls"]
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    num_urls = 250  # spans 3 chunks at the historical fixed size of 100
    urls = [f"https://example.com/p{i}" for i in range(num_urls)]

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=None,
    )

    assert fetch.failed == {}
    assert len(fetch.raw_results) == num_urls
    assert calls == [100, 100, 50]
    assert clock.sleeps == []
    assert clock.now == 0.0


# ---------------------------------------------------------------------------
# E. The burst is genuinely bounded — the property that prevents 429s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_request_exceeds_the_computed_burst_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_virtual_clock(monkeypatch)
    request_sizes: list[int] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request_sizes.append(len(payload["urls"]))
        return {
            "results": [
                {
                    "url": u,
                    "success": True,
                    "markdown": "content",
                    "links": {"internal": []},
                }
                for u in payload["urls"]
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    rate_limit = 0.25
    expected_burst = _burst_size_for(rate_limit)
    urls = [f"https://example.com/p{i}" for i in range(expected_burst * 4 + 1)]

    await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=rate_limit,
    )

    assert request_sizes, "no requests were made"
    assert all(size <= expected_burst for size in request_sizes)
