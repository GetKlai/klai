"""
Speech provider abstraction.

Protocol defines the interface.
WhisperHttpProvider is the only implementation for Phase 0-3.
Switching to a new provider = new class + STT_PROVIDER env var change.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


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


class WhisperHttpProvider:
    """Calls whisper-server POST /v1/audio/transcriptions."""

    async def transcribe(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult:
        data: dict = {}
        if language:
            data["language"] = language

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{settings.whisper_server_url}/v1/audio/transcriptions",
                    files={"file": ("audio.wav", audio_wav, "audio/wav")},
                    data=data,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("whisper-server unreachable: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Transcriptie tijdelijk niet beschikbaar",
            ) from exc

        if resp.status_code != 200:
            logger.error("whisper-server error %s: %s", resp.status_code, resp.text[:200])
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


def get_provider() -> SpeechProvider:
    if settings.stt_provider == "whisper_http":
        return WhisperHttpProvider()
    raise ValueError(f"Unknown STT provider: {settings.stt_provider}")
