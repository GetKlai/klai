"""Speech provider abstraction.

Protocol defines the interface.
WhisperHttpProvider is the only implementation for SPEC-VEXA-003.

Calls the Vexa transcription-service (OpenAI-compatible) with
``transcription_tier=deferred`` — Scribe audio uploads are best-effort
and yield to real-time meeting traffic (tier=realtime reserved slots).
On HTTP 503 we honour ``Retry-After`` up to `_MAX_RETRIES` attempts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Protocol

import httpx
import structlog
from fastapi import HTTPException, status

from app.core.config import settings

logger = structlog.get_logger(__name__)

# SPEC-VEXA-003 §5.1 — scribe uses the deferred admission tier so meetings
# (tier=realtime) get scheduler priority on the transcription-service workers.
_DEFERRED_TIER: Final = "deferred"
_MAX_RETRIES: Final = 3
_RETRY_AFTER_CLAMP: Final = (1, 30)  # seconds — lower/upper bounds for server-sent Retry-After


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


class WhisperHttpProvider:
    """Calls the Vexa transcription-service POST /v1/audio/transcriptions with tier=deferred."""

    async def transcribe(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult:
        data: dict = {"transcription_tier": _DEFERRED_TIER}
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
            except (httpx.ConnectError, httpx.TimeoutException):
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
                    )
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
                logger.error(
                    "transcription-service error",
                    status=resp.status_code,
                    body=resp.text[:200],
                    attempt=attempt,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Transcriptie tijdelijk niet beschikbaar",
                )

            payload = resp.json()
            return TranscriptionResult(
                text=payload["text"],
                language=payload["language"],
                duration_seconds=float(payload["duration"]),
                inference_time_seconds=float(payload["inference_time_seconds"]),
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
