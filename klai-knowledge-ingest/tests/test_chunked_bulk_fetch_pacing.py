"""Client-side crawl pacing: the gap between single-URL requests.

crawl4ai's REST server builds its own ``MemoryAdaptiveDispatcher`` and
ignores the ``mean_delay`` / ``semaphore_count`` we set on
``CrawlerRunConfig`` (measured live: 8 URLs with ``mean_delay=2.0``
finished in 3.2s instead of the predicted 16s). A multi-URL request is
one burst on the target site (16 URLs completed within 330ms of each
other), so every request carries one URL and real rate limiting happens
client-side through the gap between requests. This module tests that gap
in isolation, with no network calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client

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

    # Enough chunks that the fencepost effect (N chunks only have N-1 gaps
    # between them, so a handful of chunks measures slightly above nominal)
    # washes out within the margin below.
    num_urls = 1000
    urls = [f"https://example.com/p{i}" for i in range(num_urls)]

    fetch = await crawl4ai_client._chunked_bulk_fetch(
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
    gap = 1 / rate_limit
    # Chunk itself "takes" longer than the gap.
    monkeypatch.setattr(
        crawl4ai_client,
        "_crawl_sync",
        _fake_crawl_sync_factory(clock, chunk_duration=gap * 2),
    )

    urls = [f"https://example.com/p{i}" for i in range(2)]

    await crawl4ai_client._chunked_bulk_fetch(
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
    gap = 1 / rate_limit
    chunk_duration = 0.4
    monkeypatch.setattr(
        crawl4ai_client,
        "_crawl_sync",
        _fake_crawl_sync_factory(clock, chunk_duration=chunk_duration),
    )

    urls = [f"https://example.com/p{i}" for i in range(2)]

    await crawl4ai_client._chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=rate_limit,
    )

    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(gap - chunk_duration)


# ---------------------------------------------------------------------------
# D. rate_limit=None must add no sleeps; the request shape stays one URL
# per request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_rate_limit_means_no_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
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

    num_urls = 3
    urls = [f"https://example.com/p{i}" for i in range(num_urls)]

    fetch = await crawl4ai_client._chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=None,
    )

    assert fetch.failed == {}
    assert len(fetch.raw_results) == num_urls
    assert calls == [1, 1, 1]
    assert clock.sleeps == []
    assert clock.now == 0.0
