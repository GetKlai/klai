"""HY-35 / REQ-35 — stranded `processing` row reaper regression test.

Setup is mock-based: AsyncSession.execute is patched to return a hand-rolled
list of records. These tests pin the mutation contract (status flip,
error_reason assignment, audio_path preservation) and the query predicate,
which must leave rows waiting or running in the job queue alone.

See SPEC-SEC-HYGIENE-001 REQ-35.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import structlog
from sqlalchemy.dialects import postgresql


def _record(
    txn_id: str,
    *,
    status: str,
    minutes_ago: int,
    audio_path: str | None = None,
    attempts: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=txn_id,
        status=status,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        audio_path=audio_path,
        attempts=attempts,
        error_reason=None,
    )


def _sql(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _session_returning(records: list[SimpleNamespace]) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = records
    result.scalars.return_value = scalars
    session.execute.return_value = result
    return session


# ---------------------------------------------------------------------------
# REQ-35.1 — flip stranded rows to failed
# ---------------------------------------------------------------------------


async def test_reaper_flips_stranded_to_failed() -> None:
    from app.services import reaper

    stranded = _record("txn_old", status="processing", minutes_ago=35, audio_path="user1/old.wav")
    session = _session_returning([stranded])

    flipped = await reaper.reap_stranded(session, timeout_min=30)

    assert flipped == 1
    assert stranded.status == "failed"
    assert stranded.error_reason == "worker_restart_stranded"
    session.commit.assert_awaited_once()


async def test_reaper_preserves_audio_path(caplog) -> None:
    """REQ-35.3 — reaper MUST NOT delete the underlying audio file."""
    from app.services import reaper

    stranded = _record(
        "txn_old", status="processing", minutes_ago=40, audio_path="user1/preserved.wav"
    )
    session = _session_returning([stranded])

    await reaper.reap_stranded(session, timeout_min=30)

    assert stranded.audio_path == "user1/preserved.wav"


async def test_reaper_zero_stranded_skips_commit() -> None:
    """No stranded rows → no commit, no log noise."""
    from app.services import reaper

    session = _session_returning([])

    flipped = await reaper.reap_stranded(session, timeout_min=30)

    assert flipped == 0
    session.commit.assert_not_called()


async def test_reaper_flips_multiple() -> None:
    from app.services import reaper

    rows = [
        _record("a", status="processing", minutes_ago=35, audio_path="u/a.wav"),
        _record("b", status="processing", minutes_ago=120, audio_path="u/b.wav"),
        _record("c", status="processing", minutes_ago=31, audio_path=None),
    ]
    session = _session_returning(rows)

    flipped = await reaper.reap_stranded(session, timeout_min=30)

    assert flipped == 3
    assert all(r.status == "failed" for r in rows)
    assert all(r.error_reason == "worker_restart_stranded" for r in rows)
    session.commit.assert_awaited_once()


async def test_reaper_emits_structlog_event() -> None:
    """REQ-35.2 — every recovered row emits scribe_stranded_recovered with txn_id + age_minutes."""
    from app.services import reaper

    stranded = _record("txn_logged", status="processing", minutes_ago=42, audio_path="u/x.wav")
    session = _session_returning([stranded])

    with structlog.testing.capture_logs() as cap_logs:
        await reaper.reap_stranded(session, timeout_min=30)

    events = [log for log in cap_logs if log.get("event") == "scribe_stranded_recovered"]
    assert len(events) == 1
    assert events[0]["txn_id"] == "txn_logged"
    assert events[0]["age_minutes"] >= 35  # AC-35 step 8 floor


# ---------------------------------------------------------------------------
# Queue-aware predicate — only rows the job queue can no longer run
# ---------------------------------------------------------------------------


async def test_reaper_skips_queued_rows_and_reaps_rows_without_audio() -> None:
    """A processing row with audio and attempts < 3 is waiting or running in the
    queue, however old; only a row without audio or out of attempts is reaped."""
    from app.services import reaper

    session = _session_returning([])

    await reaper.reap_stranded(session, timeout_min=30)

    where = _sql(session.execute.await_args.args[0]).split("WHERE", 1)[1]
    assert "status = 'processing'" in where
    assert "audio_path IS NULL OR scribe.transcriptions.attempts >= 3 AND (" in where
    # Out of attempts but still held by a live claim: the job may still finish.
    assert "claimed_at IS NULL OR scribe.transcriptions.claimed_at <" in where
    # Rows are flipped under a row lock, skipping one a job is finishing.
    assert "FOR UPDATE SKIP LOCKED" in where
    assert "created_at <" in where
