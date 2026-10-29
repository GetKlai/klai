"""Speech provider abstraction.

Protocol defines the interface.
WhisperHttpProvider is the only implementation for SPEC-VEXA-003.

Calls the Vexa transcription-service (OpenAI-compatible) with
``transcription_tier=deferred`` — Scribe audio uploads are best-effort
and yield to real-time meeting traffic (tier=realtime reserved slots).
On HTTP 503 we honour ``Retry-After`` up to `_MAX_RETRIES` attempts.

Long recordings are split into chunks of at most
``settings.transcription_chunk_seconds`` (cut at the quietest moment shortly
before each boundary) and transcribed one after the other; the backend's body
limit and read timeout apply per request, not per recording.
"""

from __future__ import annotations

import asyncio
import io
import wave
from array import array
from dataclasses import dataclass
from typing import Final, Protocol

import httpx
import structlog
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.sanitize import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = structlog.get_logger(__name__)

# SPEC-VEXA-003 §5.1 — scribe uses the deferred admission tier so meetings
# (tier=realtime) get scheduler priority on the transcription-service workers.
_DEFERRED_TIER: Final = "deferred"
_MAX_RETRIES: Final = 3
_RETRY_AFTER_CLAMP: Final = (1, 30)  # seconds — lower/upper bounds for server-sent Retry-After
# A chunk boundary may move up to this far back to land on the quietest
# 100 ms window, so a cut does not fall in the middle of a word.
_CUT_SEARCH_SECONDS: Final = 10
_CUT_WINDOW_SECONDS: Final = 0.1


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float
    inference_time_seconds: float
    provider: str
    model: str


class SpeechProvider(Protocol):
    async def transcribe(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult: ...


def _clamped_retry_after(header_value: str | None) -> int:
    """Coerce a Retry-After header to an int in [_RETRY_AFTER_CLAMP[0], _RETRY_AFTER_CLAMP[1]]."""
    lo, hi = _RETRY_AFTER_CLAMP
    if header_value is None:
        return lo
    try:
        raw = int(header_value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(raw, hi))


def _quietest_frame(frames: bytes, lo: int, hi: int, window: int) -> int:
    """Frame index in [lo, hi) where the lowest-energy run of `window` frames starts."""
    samples = array("h")
    samples.frombytes(frames[lo * 2 : hi * 2])
    best_at, best_energy = hi, None
    for at in range(0, len(samples) - window + 1, window):
        energy = sum(abs(v) for v in samples[at : at + window])
        if best_energy is None or energy < best_energy:
            best_at, best_energy = lo + at, energy
    return best_at


def split_wav(audio_wav: bytes, max_seconds: int) -> list[bytes]:
    """Split a 16-bit mono WAV into chunks of at most `max_seconds`.

    Each cut is placed on the quietest 100 ms window in the 10 s before the
    nominal boundary. Audio shorter than `max_seconds` is returned unchanged.
    """
    with wave.open(io.BytesIO(audio_wav), "rb") as src:
        rate = src.getframerate()
        params = src.getparams()
        total = src.getnframes()
        if total <= max_seconds * rate:
            return [audio_wav]
        frames = src.readframes(total)

    chunks: list[bytes] = []
    start = 0
    while start < total:
        end = min(start + max_seconds * rate, total)
        if end < total:
            window = int(_CUT_WINDOW_SECONDS * rate)
            # The search never reaches back into the current chunk's first
            # window, so every cut makes progress even when the chunk length
            # is shorter than the search span (an operator-set value below
            # _CUT_SEARCH_SECONDS would otherwise loop forever on silence).
            end = _quietest_frame(
                frames, max(end - _CUT_SEARCH_SECONDS * rate, start + window), end, window
            )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setparams(params)
            out.writeframes(frames[start * 2 : end * 2])
        chunks.append(buf.getvalue())
        start = end
    return chunks


class WhisperHttpProvider:
    """Calls the Vexa transcription-service POST /v1/audio/transcriptions with tier=deferred."""

    async def transcribe(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult:
        chunks = split_wav(audio_wav, settings.transcription_chunk_seconds)
        results: list[TranscriptionResult] = []
        for index, chunk in enumerate(chunks):
            if len(chunks) > 1:
                logger.info("transcription_chunk", index=index + 1, total=len(chunks))
            result = await self._transcribe_chunk(chunk, language)
            results.append(result)
            # Pin the language detected on the first chunk so a quiet or
            # noisy later chunk cannot flip the transcript to another language.
            if language is None and result.language != "und":
                language = result.language
        first = results[0]
        return TranscriptionResult(
            text=" ".join(r.text.strip() for r in results if r.text.strip()),
            language=next((r.language for r in results if r.language != "und"), first.language),
            duration_seconds=sum(r.duration_seconds for r in results),
            inference_time_seconds=sum(r.inference_time_seconds for r in results),
            provider=first.provider,
            model=first.model,
        )

    async def _transcribe_chunk(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult:
        # `model` is required by the OpenAI-compatible transcription-service
        # (HTTP 422 if missing). `transcription_tier` is the Vexa extension
        # for two-tier admission control (SPEC-VEXA-003 §5.1).
        data: dict = {
            "model": settings.whisper_model,
            "transcription_tier": _DEFERRED_TIER,
        }
        if language:
            data["language"] = language

        url = f"{settings.whisper_server_url}/v1/audio/transcriptions"

        last_status: int | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    resp = await client.post(
                        url,
                        files={"file": ("audio.wav", audio_wav, "audio/wav")},
                        data=data,
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.exception(
                    "transcription-service unreachable",
                    attempt=attempt,
                    max_retries=_MAX_RETRIES,
                    url=url,
                )
                if attempt >= _MAX_RETRIES:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Transcriptie tijdelijk niet beschikbaar",
                    ) from exc
                # Exponential backoff capped at the upper Retry-After clamp.
                await asyncio.sleep(min(2 ** (attempt - 1), _RETRY_AFTER_CLAMP[1]))
                continue

            last_status = resp.status_code

            # 503 with Retry-After: honour backpressure up to _MAX_RETRIES total attempts.
            if resp.status_code == 503 and attempt < _MAX_RETRIES:
                wait_s = _clamped_retry_after(resp.headers.get("Retry-After"))
                logger.warning(
                    "transcription-service busy, retrying",
                    attempt=attempt,
                    max_retries=_MAX_RETRIES,
                    retry_after=wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code != 200:
                # SPEC-SEC-INTERNAL-001 REQ-4: scrub any reflected secret
                # before the body lands in structlog / VictoriaLogs.
                logger.error(
                    "transcription-service error",
                    status=resp.status_code,
                    body=sanitize_response_body(resp, max_len=200),
                    attempt=attempt,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Transcriptie tijdelijk niet beschikbaar",
                )

            payload = resp.json()
            # Whisper-server response fields aren't all guaranteed across
            # upstream versions. text/language/duration are part of the
            # OpenAI-compatible contract; inference_time_seconds is a Vexa
            # extension that's optional. Prefer .get() over [] for fields
            # that may be absent — a missing optional extension shouldn't
            # cause 100% transcription failure (KeyError → outer broad
            # except in transcribe.py → status='failed', root cause
            # invisible per `portal-logging-py.md` rule).
            # Discovered during e2e walkthrough 2026-05-03 — every
            # Voys-tenant scribe upload was hitting this KeyError.
            return TranscriptionResult(
                text=payload.get("text", ""),
                language=payload.get("language", "und"),
                duration_seconds=float(payload.get("duration", 0.0)),
                inference_time_seconds=float(payload.get("inference_time_seconds", 0.0)),
                provider=settings.whisper_provider_name,
                model=payload.get("model", "large-v3-turbo"),
            )

        # All _MAX_RETRIES attempts exhausted on 503 — surface backpressure upstream.
        logger.error(
            "transcription-service still busy after retries",
            max_retries=_MAX_RETRIES,
            last_status=last_status,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcriptie tijdelijk niet beschikbaar",
        )


def get_provider() -> SpeechProvider:
    if settings.stt_provider == "whisper_http":
        return WhisperHttpProvider()
    raise ValueError(f"Unknown STT provider: {settings.stt_provider}")
