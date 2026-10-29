"""POST /v1/transcribe answers with status='failed' when the backend rejects the audio.

On 2026-10-29 an 80 MB iPhone recording was rejected by the transcription
backend (413) and the user saw a bare 500: the failure branch logged through
the stdlib logger with structlog keyword arguments, which raised TypeError
after the record had already been committed as failed.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException, UploadFile

from app.api import transcribe as transcribe_api
from app.core.auth import CallerIdentity


class _RejectingProvider:
    async def transcribe(self, audio_wav: bytes, language: str | None):
        raise HTTPException(status_code=503, detail="Transcriptie tijdelijk niet beschikbaar")


async def test_backend_rejection_returns_failed_record(monkeypatch) -> None:
    monkeypatch.setattr(transcribe_api, "normalize_audio", lambda raw, filename: b"RIFFwav")
    monkeypatch.setattr(transcribe_api, "save_audio", lambda user_id, txn_id, wav: "u/t.wav")
    monkeypatch.setattr(transcribe_api, "get_provider", lambda: _RejectingProvider())
    db = AsyncMock()
    db.add = MagicMock()

    response = await transcribe_api.transcribe(
        file=UploadFile(file=io.BytesIO(b"aac-bytes"), filename="opname.m4a"),
        language=None,
        caller=CallerIdentity(user_id="user1", org_id="org1"),
        db=db,
    )

    assert response.status == "failed"
    assert response.name == "opname.m4a"
