"""
Tests for SPEC-AUTH-003: Scoped query helpers in app/services/access.py

Pure unit tests -- all async sessions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.groups import PortalGroupMembership
from app.models.meetings import VexaMeeting


def _mock_meeting(
    meeting_id: str = "m1",
    user_id: str = "alice",
    org_id: int = 1,
    group_id: int | None = None,
) -> MagicMock:
    m = MagicMock(spec=VexaMeeting)
    m.id = meeting_id
    m.zitadel_user_id = user_id
    m.org_id = org_id
    m.group_id = group_id
    return m


def _mock_db_with_scalars(rows: list) -> AsyncMock:
    """Return an AsyncMock db that returns rows from execute().scalars().all()."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    db.execute.return_value = mock_result
    return db


def _mock_db_with_scalar_subq_and_meetings(meetings: list) -> AsyncMock:
    """Simulate db.execute for get_accessible_meetings (subquery + outer query)."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = meetings
    db.execute.return_value = mock_result
    return db


# ---------------------------------------------------------------------------
# get_accessible_meetings
# ---------------------------------------------------------------------------


class TestGetAccessibleMeetings:
    @pytest.mark.asyncio
    async def test_returns_owned_meetings(self) -> None:
        from app.services.access import get_accessible_meetings

        meeting = _mock_meeting(user_id="alice", org_id=1)
        db = _mock_db_with_scalar_subq_and_meetings([meeting])

        result = await get_accessible_meetings("alice", 1, db)

        assert result == [meeting]
        db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_group_scoped_meetings(self) -> None:
        from app.services.access import get_accessible_meetings

        meeting = _mock_meeting(user_id="bob", org_id=1, group_id=42)
        db = _mock_db_with_scalar_subq_and_meetings([meeting])

        result = await get_accessible_meetings("alice", 1, db)

        assert result == [meeting]

    @pytest.mark.asyncio
    async def test_empty_result_for_no_access(self) -> None:
        from app.services.access import get_accessible_meetings

        db = _mock_db_with_scalar_subq_and_meetings([])
        result = await get_accessible_meetings("carol", 1, db)

        assert result == []


# ---------------------------------------------------------------------------
# can_write_meeting
# ---------------------------------------------------------------------------


class TestCanWriteMeeting:
    @pytest.mark.asyncio
    async def test_owner_can_write(self) -> None:
        from app.services.access import can_write_meeting

        meeting = _mock_meeting(user_id="alice")
        db = AsyncMock()

        assert await can_write_meeting("alice", meeting, db) is True

    @pytest.mark.asyncio
    async def test_non_owner_personal_meeting_cannot_write(self) -> None:
        from app.services.access import can_write_meeting

        meeting = _mock_meeting(user_id="alice", group_id=None)
        db = AsyncMock()

        assert await can_write_meeting("bob", meeting, db) is False

    @pytest.mark.asyncio
    async def test_group_admin_can_write_group_meeting(self) -> None:
        from app.services.access import can_write_meeting

        meeting = _mock_meeting(user_id="alice", group_id=42)
        db = AsyncMock()
        membership = MagicMock(spec=PortalGroupMembership)
        membership.is_group_admin = True
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = membership
        db.execute.return_value = mock_result

        assert await can_write_meeting("carol", meeting, db) is True

    @pytest.mark.asyncio
    async def test_regular_group_member_cannot_write(self) -> None:
        from app.services.access import can_write_meeting

        meeting = _mock_meeting(user_id="alice", group_id=42)
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        assert await can_write_meeting("bob", meeting, db) is False


# ---------------------------------------------------------------------------
# get_accessible_kb_slugs
# ---------------------------------------------------------------------------


class TestGetAccessibleKbSlugs:
    @pytest.mark.asyncio
    async def test_returns_base_slugs_when_no_groups(self) -> None:
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        slugs = await get_accessible_kb_slugs("alice", db)

        assert "personal-alice" in slugs
        assert "org" in slugs

    @pytest.mark.asyncio
    async def test_returns_group_slugs_for_memberships(self) -> None:
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(42,), (99,)]
        db.execute.return_value = mock_result

        slugs = await get_accessible_kb_slugs("alice", db)

        assert "group:42" in slugs
        assert "group:99" in slugs

    @pytest.mark.asyncio
    async def test_no_group_slugs_for_non_members(self) -> None:
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        slugs = await get_accessible_kb_slugs("carol", db)

        group_slugs = [s for s in slugs if s.startswith("group:")]
        assert group_slugs == []


# ---------------------------------------------------------------------------
# is_member_of_group
# ---------------------------------------------------------------------------


class TestIsMemberOfGroup:
    @pytest.mark.asyncio
    async def test_returns_true_for_member(self) -> None:
        from app.services.access import is_member_of_group

        db = AsyncMock()
        membership = MagicMock(spec=PortalGroupMembership)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = membership
        db.execute.return_value = mock_result

        assert await is_member_of_group("alice", 42, db) is True

    @pytest.mark.asyncio
    async def test_returns_false_for_non_member(self) -> None:
        from app.services.access import is_member_of_group

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        assert await is_member_of_group("bob", 42, db) is False
