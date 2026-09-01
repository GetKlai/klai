"""Regression coverage for the manual meeting-stop lifecycle."""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.meetings import VexaWebhookPayload


def _meeting(*, status: str) -> MagicMock:
    meeting = MagicMock()
    meeting.id = uuid.uuid4()
    meeting.org_id = 42
    meeting.status = status
    meeting.meeting_url = "https://meet.google.com/abc-defg-hij"
    meeting.ended_at = None
    return meeting


@pytest.mark.asyncio
async def test_stop_meeting_terminal_callback_wins_during_vexa_delete(monkeypatch) -> None:
    """A callback delivered by DELETE must not be overwritten with ``stopping``."""
    from app.api import meetings as meetings_module

    meeting = _meeting(status="recording")
    persisted = {"status": meeting.status}
    db = MagicMock()
    db.scalar = AsyncMock(return_value=meeting)

    async def _commit() -> None:
        persisted["status"] = meeting.status

    db.commit = AsyncMock(side_effect=_commit)

    async def _stop_bot(_platform: str, _native_meeting_id: str) -> None:
        # Vexa's DELETE synchronously destroys the workload and delivers bot.failed.
        assert persisted["status"] == "stopping"
        persisted["status"] = "failed"

    monkeypatch.setattr(meetings_module, "can_write_meeting", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_module.vexa, "stop_bot", _stop_bot)
    monkeypatch.setattr(meetings_module, "_build_meeting_response", AsyncMock(return_value=MagicMock()))

    await meetings_module.stop_meeting(
        meeting.id,
        perms=MagicMock(org_id=meeting.org_id, user_id="user-1"),
        db=db,
    )

    assert persisted["status"] == "failed"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_meeting_is_recoverable_when_vexa_delete_fails(monkeypatch) -> None:
    """A failed external stop still leaves a committed row for poller recovery."""
    from app.api import meetings as meetings_module

    meeting = _meeting(status="recording")
    events: list[str] = []
    db = MagicMock()
    db.scalar = AsyncMock(return_value=meeting)
    db.commit = AsyncMock(side_effect=lambda: events.append(f"commit:{meeting.status}"))

    async def _stop_bot(_platform: str, _native_meeting_id: str) -> None:
        events.append("vexa_delete")
        raise RuntimeError("Vexa unavailable")

    monkeypatch.setattr(meetings_module, "can_write_meeting", AsyncMock(return_value=True))
    monkeypatch.setattr(meetings_module.vexa, "stop_bot", _stop_bot)
    monkeypatch.setattr(meetings_module, "_build_meeting_response", AsyncMock(return_value=MagicMock()))

    await meetings_module.stop_meeting(
        meeting.id,
        perms=MagicMock(org_id=meeting.org_id, user_id="user-1"),
        db=db,
    )

    assert events == ["commit:stopping", "vexa_delete"]


async def _deliver_status_callback(
    monkeypatch, *, current_status: str, callback_status: str
) -> tuple[MagicMock, MagicMock]:
    from app.api import meetings as meetings_module
    from app.core import database as database_module

    meeting = _meeting(status=current_status)
    lookup_db = MagicMock()
    lookup_db.scalar = AsyncMock(return_value=meeting)
    lookup_db.expunge = MagicMock()
    scoped_db = MagicMock()
    scoped_db.merge = AsyncMock(return_value=meeting)
    scoped_db.commit = AsyncMock()

    @asynccontextmanager
    async def _cross_org_session():
        yield lookup_db

    @asynccontextmanager
    async def _tenant_session(_org_id: int):
        yield scoped_db

    monkeypatch.setattr(meetings_module, "_require_webhook_secret", lambda _request: None)
    monkeypatch.setattr(database_module, "cross_org_session", _cross_org_session)
    monkeypatch.setattr(database_module, "tenant_scoped_session", _tenant_session)

    payload = VexaWebhookPayload(
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        status=callback_status,
    )
    result = await meetings_module.vexa_webhook(payload, request=MagicMock(), db=AsyncMock())

    assert result == {"status": "synced"}
    return meeting, scoped_db


@pytest.mark.asyncio
async def test_terminal_failure_callback_advances_stopping_meeting(monkeypatch) -> None:
    """Vexa ``bot.failed`` is terminal even after the user requested stop."""
    meeting, scoped_db = await _deliver_status_callback(
        monkeypatch,
        current_status="stopping",
        callback_status="failed",
    )

    assert meeting.status == "failed"
    scoped_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonterminal_callback_does_not_rewind_stopping_meeting(monkeypatch) -> None:
    """A late admission callback cannot rewind a stop already in progress."""
    meeting, scoped_db = await _deliver_status_callback(
        monkeypatch,
        current_status="stopping",
        callback_status="awaiting_admission",
    )

    assert meeting.status == "stopping"
    scoped_db.commit.assert_not_awaited()
