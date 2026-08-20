"""crawl-cancel — ``_chunked_bulk_fetch`` cooperative cancellation.

Production incident (2026-08-19): ``POST .../crawl/sync/{job_id}/cancel``
returned 204 on a running crawl, but the crawl kept fetching pages for
minutes afterward (32 requests observed in the 2 minutes after cancel).
The endpoint only set Procrastinate's ``abort_requested`` column — nothing
in the crawl loop ever checked it.

The fix: ``_chunked_bulk_fetch`` accepts an optional ``cancel_check``
awaitable, checked once per chunk (same granularity as the host circuit
breaker already wired into this loop — see
``test_chunked_bulk_fetch_circuit_breaker_stop.py``), at the very top of
the loop, BEFORE the pacing sleep and before the chunk's HTTP request is
built. These tests lock in: no wasted request once cancelled, once-per-
chunk (not per-URL) checking, and that cancellation and the circuit
breaker do not interfere with each other.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import _chunked_bulk_fetch
from knowledge_ingest.reason_codes import FetchReasonCode

# At 0.5 req/s, the host gate's 10-second window allows five URLs per request — five
# URLs per chunk, so 20 URLs produce exactly four chunks.
_FIVE_URLS_PER_CHUNK_RATE_LIMIT = 0.5


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


def _disable_real_pacing_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same helper as test_chunked_bulk_fetch_circuit_breaker_stop.py — the
    real inter-chunk pacing gap (chunk_size / rate_limit seconds) would
    otherwise make these tests take real wall-clock seconds per chunk."""
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)


@pytest.mark.asyncio
async def test_cancel_requested_before_first_chunk_sends_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_check() == True from the very first loop iteration: not one
    HTTP request goes out, and every URL is reported not_fetched_cancelled."""
    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_ok_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(5)]
    cancel_check = AsyncMock(return_value=True)

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        cancel_check=cancel_check,
    )

    assert calls == [], "cancelled before the first chunk — no request may ever be sent"
    assert fetch.cancelled is True
    assert fetch.stopped_early is True
    assert fetch.not_attempted == urls
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CANCELLED.value
    assert fetch.raw_results == []
    assert fetch.failed == {}


@pytest.mark.asyncio
async def test_cancel_checked_once_per_chunk_not_per_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """20 URLs / 5 per chunk == 4 chunks. cancel_check flips true on the
    3rd check (i.e. before chunk 3 is sent) — chunks 1 and 2 must have
    already gone out, chunks 3 and 4 must never be attempted, and
    cancel_check must be called exactly 3 times (once per chunk boundary
    reached), never once per URL (which would be 20 calls)."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_ok_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(20)]
    cancel_check = AsyncMock(side_effect=[False, False, True])

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_FIVE_URLS_PER_CHUNK_RATE_LIMIT,
        cancel_check=cancel_check,
    )

    assert cancel_check.await_count == 3, (
        f"expected exactly one cancel_check() per chunk boundary reached (3), "
        f"got {cancel_check.await_count} — a per-URL check would be 20"
    )
    assert [len(c) for c in calls] == [5, 5], "only the first two chunks were sent"
    assert fetch.cancelled is True
    assert fetch.stopped_early is True
    assert fetch.not_attempted == urls[10:]
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CANCELLED.value
    # The first two chunks' real results are kept — cancellation does not
    # discard work already done.
    assert len(fetch.raw_results) == 10


@pytest.mark.asyncio
async def test_no_cancel_check_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel_check omitted (default None) — the loop never evaluates
    cancellation at all, matching pre-fix behaviour exactly."""

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"results": [_ok_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(5)]
    fetch = await _chunked_bulk_fetch(urls=urls, crawler_config={}, cookies=None)

    assert fetch.cancelled is False
    assert fetch.stopped_early is False
    assert fetch.not_attempted == []
    assert len(fetch.raw_results) == 5


@pytest.mark.asyncio
async def test_cancel_and_circuit_breaker_do_not_interfere_cancel_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site that is ALSO failing every request (would eventually trip the
    breaker) gets cancelled first — the breaker never gets the chance to
    fire, and the outcome is reported as a cancel, not a breaker stop."""

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"results": [_server_error_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(20)]
    # Cancel fires on the very first check, before the breaker has seen a
    # single observation.
    cancel_check = AsyncMock(return_value=True)

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_FIVE_URLS_PER_CHUNK_RATE_LIMIT,
        cancel_check=cancel_check,
    )

    assert fetch.cancelled is True
    assert fetch.circuit_breaker_triggered is False
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CANCELLED.value


@pytest.mark.asyncio
async def test_circuit_breaker_still_fires_when_cancel_check_present_but_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: passing a cancel_check that always returns False must not
    change the pre-existing circuit-breaker behaviour at all — the breaker
    keeps working exactly as it did before this fix existed."""
    _disable_real_pacing_sleep(monkeypatch)
    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(list(payload["urls"]))
        return {"results": [_server_error_page(u) for u in payload["urls"]]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [f"https://example.com/{i}" for i in range(50)]
    cancel_check = AsyncMock(return_value=False)

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=0.05,  # one URL per chunk, matches the breaker test file
        cancel_check=cancel_check,
    )

    assert fetch.cancelled is False
    assert fetch.circuit_breaker_triggered is True
    assert fetch.not_attempted_reason_code == FetchReasonCode.NOT_FETCHED_CIRCUIT_BREAKER_STOP.value
    assert len(calls) <= 5, (
        f"expected the breaker to stop after a handful of chunks, got {len(calls)}"
    )
