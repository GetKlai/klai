"""SPEC-VEXA-003 §5.2 — WhisperHttpProvider contract tests.

Covers:
- `tier=deferred` form field is posted
- HTTP 503 + Retry-After triggers a bounded retry, then succeeds
- Three consecutive 503s raise HTTPException(503)
- ConnectError is retried up to _MAX_RETRIES
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.services import providers
from app.services.providers import (
    _DEFERRED_TIER,
    _MAX_RETRIES,
    TranscriptionResult,
    WhisperHttpProvider,
)


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin settings so tests never depend on env."""
    monkeypatch.setattr(
        providers.settings,
        "whisper_server_url",
        "http://transcription-service.test",
        raising=False,
    )
    monkeypatch.setattr(
        providers.settings,
        "whisper_provider_name",
        "vexa-transcription-service",
        raising=False,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't actually wait during retries."""
    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", _instant)


def _success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "text": "hallo wereld",
            "language": "nl",
            "duration": 12.3,
            "inference_time_seconds": 1.1,
            "model": "large-v3-turbo",
        },
    )


def _busy_response(retry_after: str | None = "2") -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(503, headers=headers, text="busy")


class _FakeClient:
    """Stand-in for httpx.AsyncClient that returns a scripted sequence of responses."""

    def __init__(self, script: list[httpx.Response | Exception]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, files: dict, data: dict) -> httpx.Response:
        self.calls.append({"url": url, "files": files, "data": data})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def patch_client(monkeypatch: pytest.MonkeyPatch):
    """Replace httpx.AsyncClient with a scripted FakeClient."""
    def _install(script: list[httpx.Response | Exception]) -> _FakeClient:
        client = _FakeClient(script)
        monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **_: client)
        return client

    return _install


class TestTierDeferredIsPosted:
    async def test_request_carries_deferred_tier(self, patch_client) -> None:
        client = patch_client([_success_response()])
        result = await WhisperHttpProvider().transcribe(b"audio-bytes", language="nl")

        assert isinstance(result, TranscriptionResult)
        assert result.text == "hallo wereld"
        assert len(client.calls) == 1
        posted = client.calls[0]
        assert posted["url"] == "http://transcription-service.test/v1/audio/transcriptions"
        assert posted["data"]["transcription_tier"] == _DEFERRED_TIER
        assert posted["data"]["language"] == "nl"
        assert posted["files"]["file"][0] == "audio.wav"


class TestBackpressureRetry:
    async def test_503_then_200_returns_result(self, patch_client) -> None:
        client = patch_client([_busy_response(retry_after="2"), _success_response()])
        result = await WhisperHttpProvider().transcribe(b"audio", language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_three_consecutive_503s_raise_503(self, patch_client) -> None:
        # With _MAX_RETRIES=3, a persistent backpressure scenario must surface upstream.
        script = [_busy_response(retry_after="1") for _ in range(_MAX_RETRIES)]
        client = patch_client(script)

        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(b"audio", language=None)

        assert exc_info.value.status_code == 503
        # On the final attempt the retry branch is skipped and the non-200
        # branch fires — so we see exactly _MAX_RETRIES posts.
        assert len(client.calls) == _MAX_RETRIES

    async def test_retry_after_header_is_clamped(self, patch_client) -> None:
        # Server may send outrageous Retry-After values; provider must clamp.
        client = patch_client([_busy_response(retry_after="9999"), _success_response()])
        result = await WhisperHttpProvider().transcribe(b"audio", language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_missing_retry_after_uses_floor(self, patch_client) -> None:
        client = patch_client([_busy_response(retry_after=None), _success_response()])
        result = await WhisperHttpProvider().transcribe(b"audio", language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2


class TestTransportErrorRetry:
    async def test_connect_error_then_success(self, patch_client) -> None:
        client = patch_client(
            [httpx.ConnectError("nope", request=httpx.Request("POST", "http://x")), _success_response()]
        )
        result = await WhisperHttpProvider().transcribe(b"audio", language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_persistent_connect_error_raises_503(self, patch_client) -> None:
        client = patch_client(
            [httpx.ConnectError("nope", request=httpx.Request("POST", "http://x")) for _ in range(_MAX_RETRIES)]
        )
        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(b"audio", language=None)
        assert exc_info.value.status_code == 503
        assert len(client.calls) == _MAX_RETRIES


class TestNon200Non503Surfaces503:
    async def test_500_does_not_retry_and_raises_503(self, patch_client) -> None:
        # A 500 is not a backpressure signal — fail fast.
        client = patch_client([httpx.Response(500, text="kapoet")])
        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(b"audio", language=None)
        assert exc_info.value.status_code == 503
        assert len(client.calls) == 1
