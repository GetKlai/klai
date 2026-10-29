"""SPEC-VEXA-003 §5.2 — WhisperHttpProvider contract tests.

Covers:
- `tier=deferred` form field is posted
- HTTP 503 + Retry-After triggers a bounded retry, then succeeds
- Three consecutive 503s raise HTTPException(503)
- ConnectError is retried up to _MAX_RETRIES
"""

from __future__ import annotations

import io
import math
import wave

import httpx
import pytest
from fastapi import HTTPException

from app.services import providers
from app.services.providers import (
    _DEFERRED_TIER,
    _MAX_RETRIES,
    TranscriptionResult,
    WhisperHttpProvider,
    split_wav,
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
    monkeypatch.setattr(
        providers.settings,
        "whisper_model",
        "large-v3-turbo",
        raising=False,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't actually wait during retries."""

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(providers.asyncio, "sleep", _instant)


def _success_response(
    text: str = "hallo wereld", language: str = "nl", duration: float = 12.3
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "text": text,
            "language": language,
            "duration": duration,
            "inference_time_seconds": 1.1,
            "model": "large-v3-turbo",
        },
    )


_RATE = 16000


def _wav(seconds: float, quiet_ranges: list[tuple[float, float]] | None = None) -> bytes:
    """16 kHz mono int16 WAV: a loud 440 Hz tone, silent inside `quiet_ranges`."""
    frames = bytearray()
    for i in range(int(seconds * _RATE)):
        t = i / _RATE
        quiet = any(lo <= t < hi for lo, hi in quiet_ranges or [])
        sample = 0 if quiet else int(20000 * math.sin(2 * math.pi * 440 * t))
        frames += sample.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_RATE)
        out.writeframes(bytes(frames))
    return buf.getvalue()


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as src:
        return src.getnframes() / src.getframerate()


def _busy_response(retry_after: str | None = "2") -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(503, headers=headers, text="busy")


class _FakeClient:
    """Stand-in for httpx.AsyncClient that returns a scripted sequence of responses."""

    def __init__(self, script: list[httpx.Response | Exception]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def __aenter__(self) -> _FakeClient:
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
        result = await WhisperHttpProvider().transcribe(_wav(1), language="nl")

        assert isinstance(result, TranscriptionResult)
        assert result.text == "hallo wereld"
        assert len(client.calls) == 1
        posted = client.calls[0]
        assert posted["url"] == "http://transcription-service.test/v1/audio/transcriptions"
        assert posted["data"]["transcription_tier"] == _DEFERRED_TIER
        assert posted["data"]["model"] == "large-v3-turbo"
        assert posted["data"]["language"] == "nl"
        assert posted["files"]["file"][0] == "audio.wav"


class TestModelFieldIsPosted:
    """Regression guard for the Vexa transcription-service `model` requirement.

    The OpenAI-compatible POST /v1/audio/transcriptions endpoint rejects with
    HTTP 422 when `model` is missing. Production was broken when scribe-api
    only sent `transcription_tier` + optional `language`. This test pins the
    contract so a future refactor cannot silently drop it again.
    """

    async def test_request_carries_model_even_without_language(self, patch_client) -> None:
        client = patch_client([_success_response()])
        await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert len(client.calls) == 1
        posted = client.calls[0]
        assert "model" in posted["data"], (
            "model field is required by the transcription-service; missing it "
            "regresses to the HTTP 422 / status='failed' bug"
        )
        assert posted["data"]["model"] == "large-v3-turbo"
        assert "language" not in posted["data"]


class TestBackpressureRetry:
    async def test_503_then_200_returns_result(self, patch_client) -> None:
        client = patch_client([_busy_response(retry_after="2"), _success_response()])
        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_three_consecutive_503s_raise_503(self, patch_client) -> None:
        # With _MAX_RETRIES=3, a persistent backpressure scenario must surface upstream.
        script = [_busy_response(retry_after="1") for _ in range(_MAX_RETRIES)]
        client = patch_client(script)

        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert exc_info.value.status_code == 503
        # On the final attempt the retry branch is skipped and the non-200
        # branch fires — so we see exactly _MAX_RETRIES posts.
        assert len(client.calls) == _MAX_RETRIES

    async def test_retry_after_header_is_clamped(self, patch_client) -> None:
        # Server may send outrageous Retry-After values; provider must clamp.
        client = patch_client([_busy_response(retry_after="9999"), _success_response()])
        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_missing_retry_after_uses_floor(self, patch_client) -> None:
        client = patch_client([_busy_response(retry_after=None), _success_response()])
        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2


class TestTransportErrorRetry:
    async def test_connect_error_then_success(self, patch_client) -> None:
        client = patch_client(
            [
                httpx.ConnectError("nope", request=httpx.Request("POST", "http://x")),
                _success_response(),
            ]
        )
        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert result.text == "hallo wereld"
        assert len(client.calls) == 2

    async def test_persistent_connect_error_raises_503(self, patch_client) -> None:
        client = patch_client(
            [
                httpx.ConnectError("nope", request=httpx.Request("POST", "http://x"))
                for _ in range(_MAX_RETRIES)
            ]
        )
        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(_wav(1), language=None)
        assert exc_info.value.status_code == 503
        assert len(client.calls) == _MAX_RETRIES


class TestNon200Non503Surfaces503:
    async def test_500_does_not_retry_and_raises_503(self, patch_client) -> None:
        # A 500 is not a backpressure signal — fail fast.
        client = patch_client([httpx.Response(500, text="kapoet")])
        with pytest.raises(HTTPException) as exc_info:
            await WhisperHttpProvider().transcribe(_wav(1), language=None)
        assert exc_info.value.status_code == 503
        assert len(client.calls) == 1


class TestPayloadFieldsAreOptional:
    """Discovered during 2026-05-03 e2e walk: every Voys-tenant scribe
    upload was returning status='failed' because the live whisper-server
    response is missing the `inference_time_seconds` field that the
    provider was reading via `payload[...]`. The KeyError was swallowed
    by the broad except in transcribe.py and surfaced only as
    `status: failed` in the UI — root cause invisible.

    These regression tests pin the provider against future upstream
    schema-drift on optional fields. text/language/duration are part
    of the OpenAI-compatible contract; inference_time_seconds is a
    Vexa extension and should be treated as optional.
    """

    async def test_missing_inference_time_seconds_does_not_raise(self, patch_client) -> None:
        # Whisper-server response observed live on 2026-05-03 — no
        # `inference_time_seconds` field present.
        client = patch_client(
            [
                httpx.Response(
                    200,
                    json={
                        "text": "",
                        "language": "en",
                        "language_probability": 0.6,
                        "duration": 0.0,
                        "segments": [],
                    },
                )
            ]
        )

        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert isinstance(result, TranscriptionResult)
        assert result.text == ""
        assert result.language == "en"
        assert result.duration_seconds == 0.0
        assert result.inference_time_seconds == 0.0  # default for missing field
        assert len(client.calls) == 1

    async def test_minimal_response_uses_safe_defaults(self, patch_client) -> None:
        # Pathological-but-spec-allowed: only `text` is present.
        patch_client([httpx.Response(200, json={"text": "hi"})])

        result = await WhisperHttpProvider().transcribe(_wav(1), language=None)

        assert result.text == "hi"
        assert result.language == "und"
        assert result.duration_seconds == 0.0
        assert result.inference_time_seconds == 0.0
        assert result.model == "large-v3-turbo"


class TestLongAudioIsChunked:
    """A long recording is posted in chunks that stay under the transcription
    LB body limit (100 MB nginx) and its 300 s read timeout. An iPhone recording
    of 80 MB AAC became a 320 MB WAV and failed with 413 on 2026-10-29."""

    async def test_long_wav_is_split_and_results_are_merged(
        self, patch_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(providers.settings, "transcription_chunk_seconds", 20, raising=False)
        client = patch_client(
            [
                _success_response(text="een", language="nl", duration=19.0),
                _success_response(text="twee", language="nl", duration=19.0),
                _success_response(text="drie", language="nl", duration=12.0),
            ]
        )
        # Quiet just before the first boundary (20 s) and before the second
        # one, which is 20 s after the first cut.
        audio = _wav(seconds=50, quiet_ranges=[(19.0, 20.0), (38.0, 39.0)])

        result = await WhisperHttpProvider().transcribe(audio, language=None)

        assert [_wav_seconds(c["files"]["file"][1]) for c in client.calls] == [19.0, 19.0, 12.0]
        assert result.text == "een twee drie"
        assert result.language == "nl"
        assert result.duration_seconds == 50.0

    async def test_detected_language_is_pinned_for_later_chunks(
        self, patch_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(providers.settings, "transcription_chunk_seconds", 20, raising=False)
        client = patch_client(
            [
                _success_response(text="a", language="nl", duration=20.0),
                _success_response(text="b", language="nl", duration=5.0),
            ]
        )

        await WhisperHttpProvider().transcribe(
            _wav(seconds=25, quiet_ranges=[(19.0, 20.0)]), language=None
        )

        assert "language" not in client.calls[0]["data"]
        assert client.calls[1]["data"]["language"] == "nl"

    async def test_cut_lands_on_the_quiet_part_before_the_boundary(
        self, patch_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(providers.settings, "transcription_chunk_seconds", 20, raising=False)
        client = patch_client(
            [
                _success_response(text="a", duration=15.0),
                _success_response(text="b", duration=10.0),
            ]
        )
        # Loud everywhere except seconds 14-16, so the cut must land there
        # instead of at exactly 20 s.
        audio = _wav(seconds=25, quiet_ranges=[(14.0, 16.0)])

        await WhisperHttpProvider().transcribe(audio, language=None)

        first = _wav_seconds(client.calls[0]["files"]["file"][1])
        assert 14.0 <= first <= 16.0

    async def test_short_wav_is_posted_whole(self, patch_client, monkeypatch) -> None:
        monkeypatch.setattr(providers.settings, "transcription_chunk_seconds", 20, raising=False)
        client = patch_client([_success_response()])
        audio = _wav(seconds=20)

        await WhisperHttpProvider().transcribe(audio, language=None)

        assert len(client.calls) == 1
        assert client.calls[0]["files"]["file"][1] == audio

    async def test_language_detected_on_a_later_chunk_names_the_result(
        self, patch_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(providers.settings, "transcription_chunk_seconds", 20, raising=False)
        patch_client(
            [
                _success_response(text="", language="und", duration=19.0),
                _success_response(text="hallo", language="nl", duration=6.0),
            ]
        )

        result = await WhisperHttpProvider().transcribe(
            _wav(seconds=25, quiet_ranges=[(19.0, 20.0)]), language=None
        )

        assert result.language == "nl"
        assert result.text == "hallo"

    def test_split_makes_progress_when_chunks_are_shorter_than_the_search_span(self) -> None:
        # Silence in the very first window would otherwise be "the quietest
        # frame" for a boundary whose search window reaches back to frame 0,
        # and the split would never advance.
        audio = _wav(seconds=12, quiet_ranges=[(0.0, 0.1)])

        chunks = split_wav(audio, max_seconds=5)

        seconds = [_wav_seconds(c) for c in chunks]
        assert sum(seconds) == 12.0
        assert all(2.5 <= s <= 5.0 for s in seconds[:-1])
