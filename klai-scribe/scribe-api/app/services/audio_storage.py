"""Audio file persistence on the scribe volume.

Retention policy:
- Audio is saved BEFORE transcription starts so a failed transcription can be retried.
- On successful transcription, the audio file is deleted and the record's audio_path
  is cleared — retry is no longer meaningful once text exists.
- On user-initiated transcript delete, the audio is deleted regardless of status.

Invariant: only `failed` records should have an audio file on disk at rest.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException, status

from app.core.config import settings

# Defense-in-depth character whitelist for path components, mirroring the
# Zitadel sub regex from app.core.auth (HY-34). Even with HY-34 in place,
# a future writer could bypass auth and call _safe_audio_path directly —
# this regex catches that.
_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class _TranscriptionResult(Protocol):
    text: str
    language: str | None
    duration_seconds: float
    inference_time_seconds: float
    provider: str
    model: str


# ---------------------------------------------------------------------------
# Path-traversal helpers — SPEC-SEC-HYGIENE-001 REQ-33
# ---------------------------------------------------------------------------
#
# @MX:ANCHOR fan_in=multiple
# @MX:REASON: SPEC-SEC-HYGIENE-001 REQ-33.1/REQ-33.2. Every site that builds
# an audio path from user_id (or from a stored relative path) MUST route
# through these helpers. Defense-in-depth partner of HY-34 (sub regex):
# this check catches traversal even if a new auth flow returns a malformed
# `sub`; the regex catches malformed sub even if a new writer skips this
# check. If you add a new audio-file callsite, use one of these two helpers.


def _safe_audio_path(base: Path, user_id: str, txn_id: str) -> Path:
    """Build and validate the audio file path for save.

    Joins (base / user_id / {txn_id}.wav), resolves, and asserts the result
    stays under base.resolve(). Raises ValueError on traversal or empty input.

    SPEC-SEC-HYGIENE-001 REQ-33.1.
    """
    if not user_id or not txn_id:
        raise ValueError("invalid audio path: empty user_id or txn_id")
    if not _PATH_COMPONENT_PATTERN.fullmatch(user_id):
        raise ValueError(f"invalid audio path: user_id {user_id!r} not in [A-Za-z0-9_-]")
    if not _PATH_COMPONENT_PATTERN.fullmatch(txn_id):
        raise ValueError(f"invalid audio path: txn_id {txn_id!r} not in [A-Za-z0-9_-]")
    base_resolved = base.resolve()
    candidate = (base_resolved / user_id / f"{txn_id}.wav").resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(
            f"invalid audio path: {user_id!r}/{txn_id!r} escapes base"
        )
    return candidate


def _safe_stored_path(base: Path, rel: str) -> Path:
    """Validate a stored relative audio path for read or delete.

    Defense-in-depth: a corrupted DB row or a future code path that skips
    `_safe_audio_path` on save MUST not become a read or delete primitive
    targeting files outside the audio base directory.

    SPEC-SEC-HYGIENE-001 REQ-33.2.
    """
    if not rel:
        raise ValueError("invalid audio path: empty")
    base_resolved = base.resolve()
    candidate = (base_resolved / rel).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(f"invalid audio path: {rel!r} escapes base")
    return candidate


# ---------------------------------------------------------------------------
# Public API — all callsites route through the safe helpers above.
# ---------------------------------------------------------------------------


def save_audio(user_id: str, txn_id: str, wav_bytes: bytes) -> str:
    """Save WAV bytes to disk. Returns the relative path stored on the record."""
    base = Path(settings.audio_storage_dir)
    path = _safe_audio_path(base, user_id, txn_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)
    return f"{user_id}/{txn_id}.wav"


def read_audio(audio_path: str) -> bytes:
    """Read WAV bytes from disk. Raises 410 GONE if the file is absent.

    Raises ValueError if `audio_path` escapes the audio base directory.
    """
    base = Path(settings.audio_storage_dir)
    path = _safe_stored_path(base, audio_path)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Audio bestand niet meer beschikbaar",
        )
    return path.read_bytes()


def delete_audio(audio_path: str | None) -> None:
    """Delete audio file from disk if it exists. Safe to call with None.

    Raises ValueError if `audio_path` escapes the audio base directory.
    Missing file is a no-op.
    """
    if not audio_path:
        return
    base = Path(settings.audio_storage_dir)
    path = _safe_stored_path(base, audio_path)
    path.unlink(missing_ok=True)


def finalize_success(record: Any, result: _TranscriptionResult) -> None:
    """Mark a Transcription record as successful and purge its audio from disk.

    SPEC-SEC-HYGIENE-001 REQ-36.1 — order is (1) delete file, (2) mutate
    record. If `delete_audio` raises, the mutation is skipped and the caller
    (which still holds the un-mutated record) commits nothing — disk and DB
    stay consistent (file present, audio_path still set).

    Mutates `record` in place. Does NOT commit — caller owns the DB session.
    """
    audio_path = record.audio_path
    delete_audio(audio_path)

    record.status = "transcribed"
    record.text = result.text
    record.language = result.language
    record.duration_seconds = result.duration_seconds
    record.inference_time_seconds = result.inference_time_seconds
    record.provider = result.provider
    record.model = result.model
    record.audio_path = None
