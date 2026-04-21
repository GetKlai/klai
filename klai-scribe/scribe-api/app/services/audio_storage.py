"""Audio file persistence on the scribe volume.

Retention policy:
- Audio is saved BEFORE transcription starts so a failed transcription can be retried.
- On successful transcription, the audio file is deleted and the record's audio_path
  is cleared — retry is no longer meaningful once text exists.
- On user-initiated transcript delete, the audio is deleted regardless of status.

Invariant: only `failed` records should have an audio file on disk at rest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException, status

from app.core.config import settings


class _TranscriptionResult(Protocol):
    text: str
    language: str | None
    duration_seconds: float
    inference_time_seconds: float
    provider: str
    model: str


def save_audio(user_id: str, txn_id: str, wav_bytes: bytes) -> str:
    """Save WAV bytes to disk. Returns the relative path stored on the record."""
    rel = f"{user_id}/{txn_id}.wav"
    path = Path(settings.audio_storage_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)
    return rel


def read_audio(audio_path: str) -> bytes:
    """Read WAV bytes from disk. Raises 410 GONE if the file is absent."""
    path = Path(settings.audio_storage_dir) / audio_path
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Audio bestand niet meer beschikbaar",
        )
    return path.read_bytes()


def delete_audio(audio_path: str | None) -> None:
    """Delete audio file from disk if it exists. Safe to call with None or missing file."""
    if not audio_path:
        return
    path = Path(settings.audio_storage_dir) / audio_path
    path.unlink(missing_ok=True)


def finalize_success(record: Any, result: _TranscriptionResult) -> None:
    """Mark a Transcription record as successful and purge its audio from disk.

    Mutates `record` in place. Does NOT commit — caller owns the DB session.
    Deleting the file AFTER mutating the record means a crash between the two
    leaves a dangling file (recoverable on next manual delete), never a
    dangling DB reference — the audio_path field is cleared as part of the
    mutation.
    """
    record.status = "transcribed"
    record.text = result.text
    record.language = result.language
    record.duration_seconds = result.duration_seconds
    record.inference_time_seconds = result.inference_time_seconds
    record.provider = result.provider
    record.model = result.model

    audio_path = record.audio_path
    record.audio_path = None
    delete_audio(audio_path)
