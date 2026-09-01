"""
Unit tests for bot_poller.

Regression coverage for the 2026-04-24 DetachedInstanceError bug where
poll_loop iterated VexaMeeting ORM instances OUTSIDE the cross_org_session
context — triggering DetachedInstanceError on rollback-expired attributes.

The fix: snapshot primitives (id, org_id, meeting_url, status) inside the
session, iterate on those afterwards.
"""

from __future__ import annotations

import dataclasses
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.exc import DetachedInstanceError

from app.services import bot_poller

# ---------------------------------------------------------------------------
# Tracked ORM-instance stand-in
# ---------------------------------------------------------------------------


class _TrackedMeeting:
    """VexaMeeting stand-in that raises DetachedInstanceError if an ORM-column
    attribute is read AFTER the owning session has exited.

    This mirrors the real SQLAlchemy behaviour: rollback expires attributes,
    and any subsequent lazy load raises DetachedInstanceError because the
    session is gone.
    """

    _COLUMN_ATTRS = frozenset(
        {"id", "org_id", "meeting_url", "status", "ended_at", "started_at", "created_at", "vexa_meeting_id"}
    )

    def __init__(self, *, post_exit_flag: list[bool], **columns: object) -> None:
        # Use object.__setattr__ so __getattr__ doesn't trip on our init.
        object.__setattr__(self, "_post_exit_flag", post_exit_flag)
        object.__setattr__(self, "_columns", columns)

    def __getattr__(self, name: str) -> object:
        if name in self._COLUMN_ATTRS:
            if self._post_exit_flag[0]:
                raise DetachedInstanceError(
                    f"Instance is not bound to a Session; refused access to {name!r} "
                    "after cross_org_session exit (regression probe for 2026-04-24 bug)"
                )
            return self._columns[name]
        raise AttributeError(name)


def _mk_meeting(
    *,
    post_exit_flag: list[bool],
    meeting_id: uuid.UUID | None = None,
    org_id: int | None = 1,
    platform: str = "google_meet",
    native_id: str = "abc-def-ghi",
    status: str = "recording",
    started_at: datetime | None = None,
) -> _TrackedMeeting:
    started_at = started_at or datetime.now(UTC)
    return _TrackedMeeting(
        post_exit_flag=post_exit_flag,
        id=meeting_id or uuid.uuid4(),
        org_id=org_id,
        meeting_url=f"https://meet.google.com/{native_id}",
        status=status,
        ended_at=None,
        started_at=started_at,
        created_at=started_at,
        vexa_meeting_id=42,
    )


def _mk_cross_org_session(active_rows, stuck_rows, post_exit_flag: list[bool]):
    """Build an @asynccontextmanager that mimics cross_org_session().

    Yields a mock session whose execute() returns active rows first, then stuck
    rows. Flips post_exit_flag[0] = True on exit — so any ORM-column access
    afterwards raises DetachedInstanceError.
    """

    @asynccontextmanager
    async def _ctx():
        session = AsyncMock()
        active_result = MagicMock()
        active_result.scalars.return_value.all.return_value = active_rows
        stuck_result = MagicMock()
        stuck_result.scalars.return_value.all.return_value = stuck_rows
        session.execute = AsyncMock(side_effect=[active_result, stuck_result])
        try:
            yield session
        finally:
            post_exit_flag[0] = True

    return _ctx


def test_stuck_meetings_stmt_includes_rows_without_vexa_meeting_id() -> None:
    """A stopping row without vexa_meeting_id cannot be transcribed, but it must not hang forever."""
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=10)

    compiled = str(bot_poller._stuck_meetings_stmt(cutoff).compile(dialect=postgresql.dialect()))

    assert "vexa_meetings.status = %(status_1)s" in compiled
    assert "vexa_meetings.vexa_meeting_id IS NOT NULL" not in compiled
    assert "vexa_meetings.ended_at <" in compiled
    assert "vexa_meetings.created_at <" in compiled


@pytest.mark.asyncio
async def test_vexa_lifecycle_status_is_preserved_for_reconciliation(monkeypatch) -> None:
    """The poller must retain Vexa's meeting FSM status instead of reducing it to container liveness."""
    post_exit = [False]
    active = [_mk_meeting(post_exit_flag=post_exit, status="joining")]
    monkeypatch.setattr(
        bot_poller.vexa,
        "get_running_bots",
        AsyncMock(
            return_value=[
                {
                    "platform": "google_meet",
                    "native_meeting_id": "abc-def-ghi",
                    "status": "active",
                }
            ]
        ),
    )

    result = await bot_poller._fetch_running_keys_safe(active)

    assert result == {("google_meet", "abc-def-ghi"): "active"}


@pytest.mark.asyncio
async def test_legacy_runtime_status_does_not_confirm_recording(monkeypatch) -> None:
    """A legacy container-only status proves liveness but cannot prove meeting admission."""
    post_exit = [False]
    active = [_mk_meeting(post_exit_flag=post_exit, status="joining")]
    monkeypatch.setattr(
        bot_poller.vexa,
        "get_running_bots",
        AsyncMock(
            return_value=[
                {
                    "platform": "google_meet",
                    "native_meeting_id": "abc-def-ghi",
                    "status": "Up 3 minutes",
                    "normalized_status": "active",
                }
            ]
        ),
    )

    result = await bot_poller._fetch_running_keys_safe(active)

    assert result == {("google_meet", "abc-def-ghi"): None}


@pytest.mark.asyncio
async def test_handle_meeting_ended_rearms_tenant_context_after_stopping_commit(monkeypatch) -> None:
    """The interim stopping commit can release the RLS GUC; re-set it before transcription."""
    events: list[str] = []
    meeting_id = uuid.uuid4()
    meeting = MagicMock()
    meeting.id = meeting_id
    meeting.status = "recording"
    meeting.ended_at = None

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=meeting)
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield db

    async def _set_tenant(_db, _org_id):
        events.append("set_tenant")

    async def _run_transcription(_meeting):
        events.append("run_transcription")

    monkeypatch.setattr(bot_poller, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(bot_poller, "set_tenant", _set_tenant)
    monkeypatch.setattr(bot_poller, "run_transcription", _run_transcription)
    monkeypatch.setattr(bot_poller, "cleanup_recording", AsyncMock())

    snap = bot_poller._ActiveMeetingSnapshot(
        id=meeting_id,
        org_id=42,
        meeting_url="https://meet.google.com/abc-def-ghi",
        status="recording",
    )

    await bot_poller._handle_meeting_ended(snap)

    assert events == ["commit", "set_tenant", "run_transcription", "commit"]


@pytest.mark.asyncio
async def test_disappeared_unadmitted_bot_fails_without_transcription(monkeypatch) -> None:
    """A bot that vanished before any active signal did not produce a confirmed recording."""
    meeting_id = uuid.uuid4()
    meeting = MagicMock(status="joining", ended_at=None, error_message=None)
    db = MagicMock()
    db.scalar = AsyncMock(return_value=meeting)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield db

    run_transcription = AsyncMock()
    monkeypatch.setattr(bot_poller, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(bot_poller, "set_tenant", AsyncMock())
    monkeypatch.setattr(bot_poller, "run_transcription", run_transcription)
    monkeypatch.setattr(bot_poller, "cleanup_recording", AsyncMock())

    snap = bot_poller._ActiveMeetingSnapshot(
        id=meeting_id,
        org_id=42,
        meeting_url="https://meet.google.com/abc-def-ghi",
        status="joining",
    )

    await bot_poller._handle_meeting_ended(snap)

    assert meeting.status == "failed"
    assert meeting.ended_at is not None
    assert meeting.error_message == "Bot ended before recording was confirmed"
    db.commit.assert_awaited_once()
    run_transcription.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_once_does_not_access_orm_after_session_exit(monkeypatch):
    """Regression for 2026-04-24 DetachedInstanceError bug.

    poll_loop iterated VexaMeeting instances AFTER cross_org_session() exited.
    Rollback on exit expires all attributes; first access to meeting.meeting_url
    raised DetachedInstanceError ~every 10s in production.

    The fix snapshots primitives inside the session so the post-session loop
    works on a plain dataclass.
    """
    post_exit = [False]
    meeting = _mk_meeting(post_exit_flag=post_exit, status="recording")

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([meeting], [], post_exit),
    )
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={("google_meet", "abc-def-ghi"): "awaiting_admission"}),
    )
    handle_ended = AsyncMock()
    recover_stuck = AsyncMock()
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", handle_ended)
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", recover_stuck)

    # Real bug would raise DetachedInstanceError here.
    await bot_poller._poll_once()

    # Bot is still running (platform/native_id in running_keys) and status is
    # "recording" — so no helper should fire.
    handle_ended.assert_not_awaited()
    recover_stuck.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_bot_does_not_report_recording_before_admission(monkeypatch):
    """A running container proves liveness, not admission to the meeting."""
    post_exit = [False]
    mid = uuid.uuid4()
    meeting = _mk_meeting(post_exit_flag=post_exit, meeting_id=mid, status="joining", org_id=7)

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([meeting], [], post_exit),
    )
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={("google_meet", "abc-def-ghi"): "awaiting_admission"}),
    )
    tenant_session = MagicMock()
    monkeypatch.setattr(bot_poller, "tenant_scoped_session", tenant_session)
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", AsyncMock())
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    tenant_session.assert_not_called()


@pytest.mark.asyncio
async def test_active_vexa_status_reports_recording_after_admission(monkeypatch) -> None:
    """Only Vexa's active lifecycle state proves that the bot reached the meeting."""
    post_exit = [False]
    meeting = _mk_meeting(post_exit_flag=post_exit, status="joining", org_id=7)
    scoped_meeting = MagicMock(status="joining")
    scoped_db = MagicMock()
    scoped_db.scalar = AsyncMock(return_value=scoped_meeting)
    scoped_db.commit = AsyncMock()

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield scoped_db

    monkeypatch.setattr(bot_poller, "cross_org_session", _mk_cross_org_session([meeting], [], post_exit))
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={("google_meet", "abc-def-ghi"): "active"}),
    )
    monkeypatch.setattr(bot_poller, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", AsyncMock())
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    assert scoped_meeting.status == "recording"
    scoped_db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("vexa_status", ["awaiting_admission", None])
async def test_joining_meeting_times_out_even_while_vexa_bot_is_still_running(monkeypatch, vexa_status) -> None:
    """A live bot container must not keep an unadmitted meeting active forever."""
    post_exit = [False]
    started_at = datetime.now(UTC) - timedelta(seconds=181)
    meeting = _mk_meeting(post_exit_flag=post_exit, status="joining", org_id=7, started_at=started_at)
    scoped_meeting = MagicMock(status="joining", ended_at=None, error_message=None)
    scoped_db = MagicMock()
    scoped_db.scalar = AsyncMock(return_value=scoped_meeting)
    scoped_db.commit = AsyncMock()

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield scoped_db

    stop_bot = AsyncMock()
    monkeypatch.setattr(bot_poller, "cross_org_session", _mk_cross_org_session([meeting], [], post_exit))
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={("google_meet", "abc-def-ghi"): vexa_status}),
    )
    monkeypatch.setattr(bot_poller, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(bot_poller.vexa, "stop_bot", stop_bot)
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", AsyncMock())
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    assert scoped_meeting.status == "failed"
    assert scoped_meeting.ended_at is not None
    assert scoped_meeting.error_message == "Bot was not admitted within 3 minutes"
    stop_bot.assert_awaited_once_with("google_meet", "abc-def-ghi")


@pytest.mark.asyncio
async def test_joining_timeout_retries_when_vexa_stop_fails(monkeypatch) -> None:
    """Do not report a terminal timeout while the overdue upstream workload could still be running."""
    post_exit = [False]
    started_at = datetime.now(UTC) - timedelta(seconds=181)
    meeting = _mk_meeting(post_exit_flag=post_exit, status="joining", org_id=7, started_at=started_at)
    scoped_meeting = MagicMock(status="joining")
    scoped_db = MagicMock()
    scoped_db.scalar = AsyncMock(return_value=scoped_meeting)
    scoped_db.commit = AsyncMock()

    @asynccontextmanager
    async def _tenant_session(_org_id):
        yield scoped_db

    monkeypatch.setattr(bot_poller, "cross_org_session", _mk_cross_org_session([meeting], [], post_exit))
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={("google_meet", "abc-def-ghi"): "awaiting_admission"}),
    )
    monkeypatch.setattr(bot_poller, "tenant_scoped_session", _tenant_session)
    monkeypatch.setattr(bot_poller.vexa, "stop_bot", AsyncMock(side_effect=RuntimeError("upstream unavailable")))
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", AsyncMock())
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    assert scoped_meeting.status == "joining"
    scoped_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_once_handles_meeting_ended_when_bot_gone(monkeypatch):
    """Bot not in running_keys → trigger _handle_meeting_ended with a snapshot
    containing id and org_id so the helper can open its own tenant session."""
    post_exit = [False]
    mid = uuid.uuid4()
    meeting = _mk_meeting(post_exit_flag=post_exit, meeting_id=mid, org_id=9, native_id="gone-meeting")

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([meeting], [], post_exit),
    )
    monkeypatch.setattr(
        bot_poller,
        "_fetch_running_keys_safe",
        AsyncMock(return_value={}),  # empty → bot has ended
    )
    handle_ended = AsyncMock()
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", handle_ended)
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    assert handle_ended.await_count == 1
    (snap,) = handle_ended.await_args.args
    assert snap.id == mid
    assert snap.org_id == 9
    assert snap.meeting_url == "https://meet.google.com/gone-meeting"


@pytest.mark.asyncio
async def test_poll_once_recovers_stuck_meetings(monkeypatch):
    """Stuck meetings are passed as snapshots to _recover_stuck_meeting."""
    post_exit = [False]
    stuck_mid = uuid.uuid4()
    stuck_meeting = _mk_meeting(post_exit_flag=post_exit, meeting_id=stuck_mid, org_id=42, status="stopping")

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([], [stuck_meeting], post_exit),
    )
    monkeypatch.setattr(bot_poller, "_fetch_running_keys_safe", AsyncMock(return_value=None))
    recover_stuck = AsyncMock()
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", recover_stuck)
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", AsyncMock())

    await bot_poller._poll_once()

    assert recover_stuck.await_count == 1
    (snap,) = recover_stuck.await_args.args
    assert snap.id == stuck_mid
    assert snap.org_id == 42


@pytest.mark.asyncio
async def test_poll_once_skips_end_detection_when_running_keys_none(monkeypatch):
    """If Vexa's running-bots call fails (running_keys is None), end-detection
    is skipped for active meetings but stuck recovery still runs."""
    post_exit = [False]
    active = _mk_meeting(post_exit_flag=post_exit, status="recording")
    stuck_post_exit = post_exit  # share — only one session per iteration
    stuck = _mk_meeting(post_exit_flag=stuck_post_exit, status="stopping")

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([active], [stuck], post_exit),
    )
    monkeypatch.setattr(bot_poller, "_fetch_running_keys_safe", AsyncMock(return_value=None))
    handle_ended = AsyncMock()
    recover_stuck = AsyncMock()
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", handle_ended)
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", recover_stuck)

    await bot_poller._poll_once()

    handle_ended.assert_not_awaited()
    assert recover_stuck.await_count == 1


@pytest.mark.asyncio
async def test_poll_once_skips_meeting_with_unparseable_url(monkeypatch):
    """parse_meeting_url returning None must short-circuit that meeting — no
    end-detection, no upgrade, no crash."""
    post_exit = [False]
    meeting = _TrackedMeeting(
        post_exit_flag=post_exit,
        id=uuid.uuid4(),
        org_id=1,
        meeting_url="not-a-valid-meeting-url",
        status="recording",
        ended_at=None,
        started_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        vexa_meeting_id=None,
    )

    monkeypatch.setattr(
        bot_poller,
        "cross_org_session",
        _mk_cross_org_session([meeting], [], post_exit),
    )
    monkeypatch.setattr(bot_poller, "_fetch_running_keys_safe", AsyncMock(return_value={}))
    handle_ended = AsyncMock()
    monkeypatch.setattr(bot_poller, "_handle_meeting_ended", handle_ended)
    monkeypatch.setattr(bot_poller, "_recover_stuck_meeting", AsyncMock())

    await bot_poller._poll_once()

    handle_ended.assert_not_awaited()


# ---------------------------------------------------------------------------
# Snapshot dataclass contract
# ---------------------------------------------------------------------------


def test_active_meeting_snapshot_is_frozen_dataclass():
    """_ActiveMeetingSnapshot must be an immutable value — no accidental
    sharing of mutable state across poll cycles."""
    snap = bot_poller._ActiveMeetingSnapshot(id=uuid.uuid4(), org_id=1, meeting_url="https://x", status="recording")
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.status = "other"  # type: ignore[misc]


def test_stuck_meeting_timeout_cutoff_uses_processing_timeout_minutes():
    """Sanity: POLL_INTERVAL and PROCESSING_TIMEOUT_MINUTES are preserved
    after refactor — they're tuning knobs and should not silently shift."""
    assert bot_poller.POLL_INTERVAL == 10
    assert bot_poller.PROCESSING_TIMEOUT_MINUTES == 10
    # Cutoff is derived, not stored — verify the math is consistent.
    cutoff = datetime.now(UTC) - timedelta(minutes=bot_poller.PROCESSING_TIMEOUT_MINUTES)
    assert (datetime.now(UTC) - cutoff) >= timedelta(minutes=9, seconds=59)
