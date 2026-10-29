"""Queue SQL of `app.services.jobs` and `app.services.reaper` against a real Postgres.

The unit tests in test_jobs.py and test_stranded_reaper.py mock the session,
so none of the statements that carry the queue's correctness run there:
`FOR UPDATE SKIP LOCKED` (two workers never get the same row),
`make_interval(mins => :stale_minutes)` (stale-claim cutoff computed by the
server), `session.get(..., with_for_update=True)` (ownership re-read under a
row lock) and the rollback that expires ORM instances (the `MissingGreenlet`
a hand run against Postgres 18 found, which no mock reproduced).

Runs only when `SCRIBE_TEST_DSN` points at a throwaway database, e.g.
`postgresql+asyncpg://klai:klai@localhost:5432/scribe_test`; CI provides one as
a service container. Tables come from the models, not alembic: migrations
0004 and 0008 depend on tables this database does not have.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.transcription import Base, Transcription
from app.services import audio_storage, jobs
from app.services.reaper import reap_stranded
from tests._job_fakes import Result, make_record

_DSN = os.environ.get("SCRIBE_TEST_DSN")
if not _DSN:
    pytest.skip("SCRIBE_TEST_DSN not set (needs a real Postgres)", allow_module_level=True)


def _ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


@pytest_asyncio.fixture
async def factory():
    # One engine per test: asyncpg connections are bound to the test's event loop.
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS scribe"))
        await conn.run_sync(Base.metadata.create_all, tables=[Transcription.__table__])
        await conn.execute(text("TRUNCATE scribe.transcriptions"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _insert(factory, *ids: str, **overrides) -> None:
    async with factory() as session:
        session.add_all(make_record(i, **overrides) for i in ids)
        await session.commit()


async def _row(factory, txn_id: str) -> Transcription | None:
    async with factory() as session:
        return await session.get(Transcription, txn_id, populate_existing=True)


async def _claim_one(factory) -> str | None:
    async with factory() as session:
        return await jobs._claim(session)


async def test_concurrent_claims_get_different_rows_in_created_at_order(factory):
    async with factory() as session:
        # Inserted out of order so created_at, not insertion order, decides.
        session.add_all(
            [
                make_record("c", created_at=_ago(1)),
                make_record("a", created_at=_ago(3)),
                make_record("b", created_at=_ago(2)),
            ]
        )
        await session.commit()

    async with factory() as s1, factory() as s2:
        first, second = await asyncio.gather(jobs._claim(s1), jobs._claim(s2))

    assert sorted([first, second]) == ["a", "b"]
    for txn_id in ("a", "b"):
        row = await _row(factory, txn_id)
        assert row.attempts == 1
        assert row.claimed_at is not None
    untouched = await _row(factory, "c")
    assert untouched.attempts == 0
    assert untouched.claimed_at is None


async def test_claim_skips_live_claims_and_takes_over_stale_ones(factory):
    await _insert(factory, "live", claimed_at=_ago(jobs.STALE_MINUTES - 10), attempts=1)
    assert await _claim_one(factory) is None

    await _insert(factory, "stale", claimed_at=_ago(jobs.STALE_MINUTES + 10), attempts=1)
    assert await _claim_one(factory) == "stale"
    row = await _row(factory, "stale")
    assert row.attempts == 2
    assert row.claimed_at > _ago(1)


async def test_claim_ignores_rows_the_queue_cannot_run(factory):
    await _insert(factory, "exhausted", attempts=jobs.MAX_ATTEMPTS)
    await _insert(factory, "no_audio", audio_path=None)
    await _insert(factory, "done", status="transcribed")
    await _insert(factory, "failed", status="failed")

    assert await _claim_one(factory) is None
    for txn_id in ("exhausted", "no_audio", "done", "failed"):
        assert (await _row(factory, txn_id)).claimed_at is None


async def test_finish_applies_only_while_the_claim_is_still_ours(factory):
    await _insert(factory, "t")
    assert await _claim_one(factory) == "t"
    claimed_at = (await _row(factory, "t")).claimed_at

    async with factory() as other:
        await other.execute(
            text("UPDATE scribe.transcriptions SET claimed_at = :ts WHERE id = 't'"),
            {"ts": _ago(0)},
        )
        await other.commit()
    takeover_claimed_at = (await _row(factory, "t")).claimed_at
    assert takeover_claimed_at != claimed_at

    async with factory() as session:
        assert await jobs._finish(session, "t", claimed_at, jobs._fail("boom")) == "overtaken"
        # The session stays usable after the rollback inside _finish.
        reread = await session.get(Transcription, "t", populate_existing=True)
        assert reread.status == "processing"
    row = await _row(factory, "t")
    assert row.error_reason is None
    assert row.claimed_at == takeover_claimed_at

    async with factory() as session:
        assert await jobs._finish(session, "t", takeover_claimed_at, jobs._fail("boom")) == "failed"
    row = await _row(factory, "t")
    assert (row.status, row.error_reason) == ("failed", "boom")


async def test_finish_on_a_row_deleted_meanwhile_returns_deleted(factory):
    await _insert(factory, "t")
    assert await _claim_one(factory) == "t"

    async with factory() as session:
        claimed_at = (await session.get(Transcription, "t", populate_existing=True)).claimed_at
        async with factory() as other:
            await other.execute(text("DELETE FROM scribe.transcriptions WHERE id = 't'"))
            await other.commit()
        assert await jobs._finish(session, "t", claimed_at, jobs._fail("boom")) == "deleted"
    assert await _row(factory, "t") is None


async def test_startup_recovery_fails_exhausted_rows_and_releases_the_rest(factory, monkeypatch):
    monkeypatch.setattr(jobs, "AsyncSessionLocal", factory)
    await _insert(factory, "dead", attempts=3, claimed_at=_ago(jobs.STALE_MINUTES + 10))
    await _insert(factory, "running", attempts=3, claimed_at=_ago(1))
    await _insert(factory, "mid", attempts=1, claimed_at=_ago(1))
    await _insert(factory, "fresh")

    await jobs._recover_on_startup()

    dead = await _row(factory, "dead")
    assert (dead.status, dead.error_reason) == ("failed", "attempts_exhausted")
    async with factory() as session:
        result = await session.execute(
            select(Transcription).where(Transcription.status == "processing")
        )
        processing = result.scalars().all()
    assert sorted(r.id for r in processing) == ["fresh", "mid", "running"]
    assert all(r.claimed_at is None for r in processing)


async def test_reaper_fails_only_rows_the_queue_can_no_longer_run(factory):
    old = _ago(60)
    await _insert(factory, "queued", created_at=old, attempts=1)
    await _insert(factory, "no_audio", created_at=old, audio_path=None)
    await _insert(factory, "held", created_at=old, attempts=3, claimed_at=_ago(1))
    await _insert(factory, "exhausted", created_at=old, attempts=3, claimed_at=_ago(20))

    async with factory() as session:
        assert await reap_stranded(session, timeout_min=10) == 2

    assert (await _row(factory, "queued")).status == "processing"
    assert (await _row(factory, "held")).status == "processing"
    for txn_id in ("no_audio", "exhausted"):
        reaped = await _row(factory, txn_id)
        assert (reaped.status, reaped.error_reason) == ("failed", "worker_restart_stranded")


async def test_reaper_skips_a_row_a_job_is_finishing(factory):
    """The finisher holds the row lock while it writes; the reaper must skip
    that row instead of waiting for it and flipping it afterwards."""
    await _insert(factory, "finishing", created_at=_ago(60), audio_path=None)

    async with factory() as holder:
        await holder.execute(
            text("SELECT id FROM scribe.transcriptions WHERE id = 'finishing' FOR UPDATE")
        )
        async with factory() as session:
            reaped = await asyncio.wait_for(reap_stranded(session, timeout_min=10), timeout=5)
        await holder.rollback()

    assert reaped == 0
    assert (await _row(factory, "finishing")).status == "processing"


async def test_claim_skips_a_row_locked_by_another_transaction(factory):
    """SKIP LOCKED: a row held under an uncommitted lock is passed over, not
    waited for, so a second worker takes the next row at once."""
    await _insert(factory, "locked", created_at=_ago(3))
    await _insert(factory, "free", created_at=_ago(2))

    async with factory() as holder:
        await holder.execute(
            text("SELECT id FROM scribe.transcriptions WHERE id = 'locked' FOR UPDATE")
        )
        async with factory() as session:
            claimed = await asyncio.wait_for(jobs._claim(session), timeout=5)
        await holder.rollback()

    assert claimed == "free"


async def test_run_job_transcribes_and_deletes_the_audio(factory, monkeypatch, tmp_path: Path):
    class Provider:
        async def transcribe(self, wav_bytes: bytes, language_hint: str | None):
            assert wav_bytes == b"RIFFtest"
            return Result()

    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(tmp_path))
    monkeypatch.setattr(jobs, "get_provider", lambda: Provider())
    audio = tmp_path / "user1" / "t.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"RIFFtest")
    await _insert(factory, "t")

    async with factory() as session:
        assert await jobs._claim(session) == "t"
        await jobs.run_job(session, "t")

    row = await _row(factory, "t")
    assert (row.status, row.text, row.audio_path) == ("transcribed", Result.text, None)
    assert not audio.exists()
