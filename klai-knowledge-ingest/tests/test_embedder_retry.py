"""Tests for audit-2026-05-06 finding 6: TEI embed retry budget.

Pin the contract:
- 5 attempts (was 3) — covers the 15-45s BGE-M3 model-reload window
- full jitter on backoff — random.uniform(0, min(2**attempt, 30))
- final exhaustion logs at error level — fires obs-001-ingest-error-rate-elevated
- success short-circuits without retry/sleep
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog.testing

from knowledge_ingest.embedder import (
    _MAX_ATTEMPTS,
    _MAX_BACKOFF_SECONDS,
    _embed_batch,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_success_response() -> MagicMock:
    """A 200 OK response from /v1/embeddings."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock(return_value=None)
    resp.json = MagicMock(
        return_value={
            "data": [
                {"index": 0, "embedding": [0.1] * 4},
                {"index": 1, "embedding": [0.2] * 4},
            ]
        }
    )
    return resp


def _make_5xx_error(status: int = 503) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://tei/v1/embeddings")
    response = httpx.Response(status, request=request)
    response.raise_for_status()  # binds the request — should not raise here
    # Construct the actual error
    return httpx.HTTPStatusError(f"server error {status}", request=request, response=response)


# ---------------------------------------------------------------------------
# Constants pinned by tests
# ---------------------------------------------------------------------------


def test_max_attempts_is_5():
    """Drop below 5 and the BGE-M3 reload window is no longer covered."""
    assert _MAX_ATTEMPTS == 5


def test_max_backoff_seconds_is_30():
    """Cap exists to bound worst-case sleep regardless of attempt count."""
    assert _MAX_BACKOFF_SECONDS == 30


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_attempt_success_no_sleep():
    """A successful first attempt returns embeddings immediately, no sleep."""
    client = MagicMock()
    client.post = AsyncMock(return_value=_make_success_response())

    with patch("knowledge_ingest.embedder.asyncio.sleep") as mock_sleep:
        result = await _embed_batch(client, ["hello", "world"])

    assert result == [[0.1] * 4, [0.2] * 4]
    assert client.post.await_count == 1
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Recovery path (transient failure → eventual success)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_recovers_after_transient_timeout():
    """One ReadTimeout, then success — call succeeds, sleep called once."""
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("simulated timeout"),
            _make_success_response(),
        ]
    )

    with patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await _embed_batch(client, ["x", "y"])

    assert result == [[0.1] * 4, [0.2] * 4]
    assert client.post.await_count == 2
    assert mock_sleep.await_count == 1


# ---------------------------------------------------------------------------
# Exhaustion path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausts_5_attempts_on_persistent_timeout():
    """Persistent ReadTimeout: exactly _MAX_ATTEMPTS calls + 5 sleeps + raise."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("always fails"))

    with patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(httpx.ReadTimeout):
            await _embed_batch(client, ["x", "y"])

    assert client.post.await_count == _MAX_ATTEMPTS
    assert mock_sleep.await_count == _MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_final_exhaustion_logs_at_error_level():
    """The post-loop event is at error level so obs-001 fires.

    Lock in: tei_embed_failed_max_attempts must log_level=='error' and
    carry attempts, error_type, error_message as structured fields.
    """
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("always fails"))

    with patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock):
        with structlog.testing.capture_logs() as captured:
            with pytest.raises(httpx.ReadTimeout):
                await _embed_batch(client, ["x", "y"])

    final_events = [e for e in captured if e.get("event") == "tei_embed_failed_max_attempts"]
    assert len(final_events) == 1, (
        f"expected exactly one tei_embed_failed_max_attempts event, got "
        f"{len(final_events)}: {final_events}"
    )
    final = final_events[0]
    assert final["log_level"] == "error", (
        f"final exhaustion must log at error (fires obs-001 alert), got "
        f"log_level={final.get('log_level')!r}"
    )
    assert final["attempts"] == _MAX_ATTEMPTS
    assert final["error_type"] == "ReadTimeout"
    assert "always fails" in final["error_message"]


@pytest.mark.asyncio
async def test_4xx_does_not_retry():
    """A 4xx is a client error — no retry, raise immediately."""
    request = httpx.Request("POST", "http://tei/v1/embeddings")
    response_400 = httpx.Response(400, request=request)

    def _raise_400():
        raise httpx.HTTPStatusError("bad request", request=request, response=response_400)

    bad_resp = MagicMock()
    bad_resp.raise_for_status = MagicMock(side_effect=_raise_400)

    client = MagicMock()
    client.post = AsyncMock(return_value=bad_resp)

    with patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(httpx.HTTPStatusError):
            await _embed_batch(client, ["x"])

    # 4xx must NOT trigger any retry
    assert client.post.await_count == 1
    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Jitter — the thundering-herd protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_uses_full_jitter():
    """random.uniform(0, min(2**attempt, _MAX_BACKOFF_SECONDS)) per attempt.

    Without jitter, a bulk-sync of 50 pages would wake all retries at
    identical wall-clock t=1s, t=3s, t=7s during a TEI restart and
    re-create the herd. Jitter spreads the retries over a window.
    """
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("always fails"))

    captured_uniform_args: list[tuple[float, float]] = []

    real_uniform_return = 0.001  # very small so test is fast

    def _spy_uniform(low: float, high: float) -> float:
        captured_uniform_args.append((low, high))
        return real_uniform_return

    with (
        patch("knowledge_ingest.embedder.random.uniform", side_effect=_spy_uniform),
        patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock),
    ):
        with pytest.raises(httpx.ReadTimeout):
            await _embed_batch(client, ["x"])

    # One uniform() call per attempt that triggered a sleep — that's
    # all _MAX_ATTEMPTS iterations because each one fails before reaching
    # the return.
    assert len(captured_uniform_args) == _MAX_ATTEMPTS
    # Full-jitter contract: lower bound is 0, upper bound is
    # min(2**attempt, _MAX_BACKOFF_SECONDS).
    expected_uppers = [min(2**attempt, _MAX_BACKOFF_SECONDS) for attempt in range(_MAX_ATTEMPTS)]
    actual_uppers = [high for (_low, high) in captured_uniform_args]
    actual_lowers = [low for (low, _high) in captured_uniform_args]
    assert actual_lowers == [0] * _MAX_ATTEMPTS, (
        f"jitter must start at 0 (full-jitter pattern), got {actual_lowers}"
    )
    assert actual_uppers == expected_uppers, (
        f"jitter uppers must be 2**attempt capped at {_MAX_BACKOFF_SECONDS}: "
        f"expected {expected_uppers}, got {actual_uppers}"
    )


@pytest.mark.asyncio
async def test_5xx_path_uses_jitter_and_logs_status():
    """Same retry budget + jitter for HTTP 5xx as for timeouts."""
    request = httpx.Request("POST", "http://tei/v1/embeddings")
    response_503 = httpx.Response(503, request=request)

    def _raise_503():
        raise httpx.HTTPStatusError("service unavailable", request=request, response=response_503)

    bad_resp = MagicMock()
    bad_resp.raise_for_status = MagicMock(side_effect=_raise_503)

    client = MagicMock()
    client.post = AsyncMock(return_value=bad_resp)

    with (
        patch("knowledge_ingest.embedder.random.uniform", return_value=0.001),
        patch("knowledge_ingest.embedder.asyncio.sleep", new_callable=AsyncMock),
    ):
        with structlog.testing.capture_logs() as captured:
            with pytest.raises(httpx.HTTPStatusError):
                await _embed_batch(client, ["x"])

    assert client.post.await_count == _MAX_ATTEMPTS

    # Per-attempt warnings carry status=503
    warning_events = [e for e in captured if e.get("event") == "tei_embed_5xx"]
    assert len(warning_events) == _MAX_ATTEMPTS
    for evt in warning_events:
        assert evt["status"] == 503
        assert evt["max_attempts"] == _MAX_ATTEMPTS

    # Final error event carries last_status=503
    finals = [e for e in captured if e.get("event") == "tei_embed_failed_max_attempts"]
    assert len(finals) == 1
    assert finals[0]["last_status"] == 503
    assert finals[0]["log_level"] == "error"
