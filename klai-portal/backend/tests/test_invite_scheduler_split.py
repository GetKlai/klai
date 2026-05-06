# SPEC-TI-010A C-3: invite_scheduler split cross_org SELECT vs tenant INSERT.
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch


def _make_invite(uid: str = "test-uid-001", org_id: int = 42) -> MagicMock:
    invite = MagicMock()
    invite.uid = uid
    invite.org_id = org_id
    invite.bot_name = "Klai Bot"
    invite.meeting_url = "https://meet.example.com/abc"
    invite.user_id = "user-a"
    invite.meeting_id = "meeting-001"
    return invite


class TestJoinMeetingSessionSplit:
    async def test_dedup_uses_cross_org_and_insert_uses_tenant(self) -> None:
        cross_org_called = False
        tenant_called = False

        @asynccontextmanager
        async def fake_cross_org():
            nonlocal cross_org_called
            cross_org_called = True
            db = MagicMock()
            db.scalar = AsyncMock(return_value=None)
            yield db

        @asynccontextmanager
        async def fake_tenant(org_id: int):
            nonlocal tenant_called
            tenant_called = True
            db = MagicMock()
            db.add = MagicMock()
            db.commit = AsyncMock()
            db.refresh = AsyncMock()
            yield db

        async def fake_start_bot(*_a, **_kw):
            pass

        invite = _make_invite()
        with (
            patch("app.services.invite_scheduler.cross_org_session", fake_cross_org),
            patch("app.services.invite_scheduler.tenant_scoped_session", fake_tenant),
            patch(
                "app.services.invite_scheduler._start_vexa_bot",
                new=fake_start_bot,
            ),
        ):
            from app.services.invite_scheduler import _join_meeting

            await _join_meeting(invite)

        assert cross_org_called, "cross_org_session must be used for dedup SELECT (C-3)"
        assert tenant_called, "tenant_scoped_session must be used for INSERT (C-3)"

    async def test_dedup_short_circuits_when_meeting_exists(self) -> None:
        tenant_calls = 0

        @asynccontextmanager
        async def fake_cross_org():
            db = MagicMock()
            db.scalar = AsyncMock(return_value=99)
            yield db

        @asynccontextmanager
        async def fake_tenant(org_id: int):
            nonlocal tenant_calls
            tenant_calls += 1
            db = MagicMock()
            db.add = MagicMock()
            db.commit = AsyncMock()
            yield db

        invite = _make_invite()
        with (
            patch("app.services.invite_scheduler.cross_org_session", fake_cross_org),
            patch("app.services.invite_scheduler.tenant_scoped_session", fake_tenant),
        ):
            from app.services.invite_scheduler import _join_meeting

            await _join_meeting(invite)

        assert tenant_calls == 0, "INSERT must NOT run when meeting already exists"

    async def test_tenant_session_receives_invite_org_id(self) -> None:
        received_org_id = None

        @asynccontextmanager
        async def fake_cross_org():
            db = MagicMock()
            db.scalar = AsyncMock(return_value=None)
            yield db

        @asynccontextmanager
        async def fake_tenant(org_id: int):
            nonlocal received_org_id
            received_org_id = org_id
            db = MagicMock()
            db.add = MagicMock()
            db.commit = AsyncMock()
            db.refresh = AsyncMock()
            yield db

        async def fake_start_bot(*_a, **_kw):
            pass

        invite = _make_invite(org_id=77)
        with (
            patch("app.services.invite_scheduler.cross_org_session", fake_cross_org),
            patch("app.services.invite_scheduler.tenant_scoped_session", fake_tenant),
            patch(
                "app.services.invite_scheduler._start_vexa_bot",
                new=fake_start_bot,
            ),
        ):
            from app.services.invite_scheduler import _join_meeting

            await _join_meeting(invite)

        assert received_org_id == 77, "tenant_scoped_session must be called with invite.org_id"
