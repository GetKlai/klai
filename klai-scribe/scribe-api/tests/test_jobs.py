"""Background transcription job: overlap-safe transitions, startup recovery, shutdown.

During a deploy the old and the new container can run the same job for a few
seconds. The second finisher must leave a row alone once it is no longer
`processing`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.services import audio_storage, jobs
from tests._job_fakes import FakeSession, Result, make_record


def _sql(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.fixture
def audio_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(tmp_path))
    (tmp_path / "user1").mkdir()
    (tmp_path / "user1" / "txn_a.wav").write_bytes(b"RIFFwav")
    return tmp_path


async def test_finisher_does_not_overwrite_a_row_finished_meanwhile(
    audio_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = make_record()
    session = FakeSession({record.id: record})

    class _OvertakenProvider:
        async def transcribe(self, audio_wav: bytes, language: str | None):
            # The other container finishes first.
            record.status = "transcribed"
            record.text = "first finisher"
            return Result()

    monkeypatch.setattr(jobs, "get_provider", lambda: _OvertakenProvider())

    await jobs.run_job(session, record.id)

    assert record.status == "transcribed"
    assert record.text == "first finisher"
    assert (audio_dir / "user1" / "txn_a.wav").exists()


async def test_missing_audio_on_processing_row_marks_audio_missing(
    audio_dir: Path,
) -> None:
    (audio_dir / "user1" / "txn_a.wav").unlink()
    record = make_record()
    session = FakeSession({record.id: record})

    await jobs.run_job(session, record.id)

    assert record.status == "failed"
    assert record.error_reason == "audio_missing"


async def test_missing_audio_on_finished_row_leaves_it_alone(
    audio_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = make_record()
    session = FakeSession({record.id: record})

    def _purged_by_first_finisher(_path: str) -> bytes:
        record.status = "transcribed"
        raise HTTPException(status_code=410, detail="Audio bestand niet meer beschikbaar")

    monkeypatch.setattr(jobs, "read_audio", _purged_by_first_finisher)

    await jobs.run_job(session, record.id)

    assert record.status == "transcribed"
    assert record.error_reason is None


async def test_startup_releases_claims_and_fails_exhausted_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()
    monkeypatch.setattr(jobs, "AsyncSessionLocal", lambda: session)

    await jobs._recover_on_startup()

    release, exhaust = (_sql(call.args[0]) for call in session.execute.await_args_list)
    assert "SET claimed_at=NULL" in release
    assert "status = 'processing'" in release
    assert "status='failed'" in exhaust
    assert "error_reason='attempts_exhausted'" in exhaust
    assert "attempts >= 3" in exhaust
    session.commit.assert_awaited_once()


async def test_workers_claim_with_skip_locked_and_stop_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession({})
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    monkeypatch.setattr(jobs, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(jobs, "_recover_on_startup", AsyncMock())

    await jobs.start()
    workers = list(jobs._workers)
    await asyncio.sleep(0.05)
    await jobs.stop()

    assert len(workers) == jobs.settings.transcription_workers
    assert all(task.cancelled() for task in workers)
    assert not jobs._workers
    claim_sql = str(session.execute.await_args_list[0].args[0])
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "attempts < " in claim_sql
    assert "claimed_at" in claim_sql
