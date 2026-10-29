"""POST /v1/transcribe and /retry answer within the wait budget while the job runs on.

The portal BFF gives up after 300 s, and a long recording on a busy GPU takes
longer than that, so the user saw a 504 while scribe kept working. The routes
now queue the transcription and return the record as it is after at most
`transcribe_wait_seconds`.
"""

from __future__ import annotations

import asyncio
import io
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from app.api import transcribe as transcribe_api
from app.core.auth import CallerIdentity
from app.core.config import settings
from app.services import audio_storage, jobs
from tests._job_fakes import FakeSession, Result, fake_claim, make_record

_CALLER = CallerIdentity(user_id="user1", org_id="org1")


class _FastProvider:
    async def transcribe(self, audio_wav: bytes, language: str | None):
        return Result()


class _SlowProvider:
    async def transcribe(self, audio_wav: bytes, language: str | None):
        await asyncio.sleep(1.0)
        return Result()


class _RejectingProvider:
    async def transcribe(self, audio_wav: bytes, language: str | None):
        raise HTTPException(status_code=503, detail="Transcriptie tijdelijk niet beschikbaar")


@pytest.fixture
async def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One running worker over an in-memory store, audio on a temp dir."""
    store: dict = {}
    session = FakeSession(store)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(tmp_path))
    monkeypatch.setattr(transcribe_api, "normalize_audio", lambda raw, filename: b"RIFFwav")
    monkeypatch.setattr(jobs, "AsyncSessionLocal", lambda: session)
    wake = asyncio.Event()
    monkeypatch.setattr(jobs, "_wake", wake)
    worker = asyncio.create_task(jobs._worker(wake, claim=fake_claim(store)))
    yield session
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)


async def _upload(session: FakeSession, filename: str = "opname.m4a"):
    return await transcribe_api.transcribe(
        file=UploadFile(file=io.BytesIO(b"aac-bytes"), filename=filename),
        language=None,
        caller=_CALLER,
        db=session,
    )


async def _settled(session: FakeSession, txn_id: str):
    for _ in range(100):
        record = session.store[txn_id]
        if record.status != "processing":
            return record
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish")


async def test_slow_job_answers_processing_within_budget_and_finishes_later(
    queue: FakeSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "transcribe_wait_seconds", 0.2)
    monkeypatch.setattr(jobs, "get_provider", lambda: _SlowProvider())

    started = time.monotonic()
    response = await _upload(queue)
    elapsed = time.monotonic() - started

    assert response.status == "processing"
    assert elapsed < 0.9
    record = await _settled(queue, response.id)
    assert record.status == "transcribed"
    assert record.text == Result.text
    assert record.audio_path is None
    assert not (tmp_path / "user1" / f"{response.id}.wav").exists()


async def test_fast_job_answers_transcribed_straight_away(
    queue: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "transcribe_wait_seconds", 5)
    monkeypatch.setattr(jobs, "get_provider", lambda: _FastProvider())

    response = await _upload(queue, filename="recording.webm")

    assert response.status == "transcribed"
    assert response.text == Result.text
    # Generic recorder filenames are replaced by the first words of the text.
    assert response.name == "hallo dit is een korte opname over het…"


async def test_backend_rejection_marks_failed_and_route_still_answers(
    queue: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An 80 MB recording rejected by the backend (413/503) once gave the user a bare 500."""
    monkeypatch.setattr(settings, "transcribe_wait_seconds", 5)
    monkeypatch.setattr(jobs, "get_provider", lambda: _RejectingProvider())

    response = await _upload(queue)

    assert response.status == "failed"
    assert response.name == "opname.m4a"
    record = queue.store[response.id]
    assert record.error_reason == "transcription_error"
    assert record.audio_path is not None


async def test_retry_resets_claim_and_attempts_and_requeues(
    queue: FakeSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "transcribe_wait_seconds", 5)
    monkeypatch.setattr(jobs, "get_provider", lambda: _FastProvider())
    (tmp_path / "user1").mkdir()
    (tmp_path / "user1" / "txn_old.wav").write_bytes(b"RIFFwav")
    # Exhausted and freshly claimed: without the reset no worker may claim it.
    record = make_record("txn_old", status="failed", attempts=3, claimed_at=datetime.now(UTC))
    queue.store[record.id] = record
    queue.select_result = record

    response = await transcribe_api.retry_transcription(
        txn_id="txn_old", language="nl", caller=_CALLER, db=queue
    )

    assert response.status == "transcribed"
    assert record.attempts == 1
    assert record.language_hint == "nl"


async def test_retry_on_a_processing_row_is_refused(queue: FakeSession) -> None:
    """The queue owns a processing row; a second job on the same audio is never started."""
    record = make_record("txn_busy", status="processing", claimed_at=datetime.now(UTC))
    queue.store[record.id] = record
    queue.select_result = record

    with pytest.raises(HTTPException) as exc_info:
        await transcribe_api.retry_transcription(
            txn_id="txn_busy", language=None, caller=_CALLER, db=queue
        )

    assert exc_info.value.status_code == 409
    assert record.claimed_at is not None


async def test_wait_budget_counts_from_the_start_of_the_request(
    queue: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large upload that spent the budget on decoding is answered `processing`
    at once instead of waiting another full budget on top."""
    monkeypatch.setattr(settings, "transcribe_wait_seconds", 0.2)
    monkeypatch.setattr(jobs, "get_provider", lambda: _SlowProvider())

    # The route runs normalize_audio in an executor; a sync sleep stands in
    # for a long ffmpeg decode.
    monkeypatch.setattr(
        transcribe_api, "normalize_audio", lambda raw, filename: time.sleep(0.3) or b"RIFFwav"
    )

    started = time.monotonic()
    response = await _upload(queue)
    elapsed = time.monotonic() - started

    assert response.status == "processing"
    assert 0.3 <= elapsed < 0.5  # decode time only, no extra wait
