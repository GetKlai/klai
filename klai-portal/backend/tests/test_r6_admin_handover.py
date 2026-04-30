"""
R6 tests -- admin handover: promote-admin, demote-admin, DELETE /api/admin/users/me
(SPEC-AUTH-009 R6 + C6.1/C6.2/C6.3/C6.7).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_user(uid: str = "u1", role: str = "member") -> MagicMock:
    u = MagicMock()
    u.zitadel_user_id = uid
    u.org_id = 1
    u.role = role
    u.status = "active"
    return u


def _make_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.name = "Acme"
    org.seats = 10
    return org


def _make_db(
    caller: MagicMock,
    org: MagicMock,
    target: MagicMock | None = None,
    admin_count: int = 2,
) -> AsyncMock:
    """Return an AsyncMock DB where:
    - _get_caller_org returns (caller.zitadel_user_id, org, caller)
    - a secondary execute() for the target lookup returns target (or None)
    - a scalar() for admin count returns admin_count
    """
    db = AsyncMock()
    db.add = MagicMock()

    target_result = MagicMock()
    target_result.scalar_one_or_none.return_value = target

    call_order: list[MagicMock] = []

    async def _execute(stmt, *args, **kwargs):
        mock_result = MagicMock()
        if not call_order:
            # first call in promote/demote is the target lookup
            mock_result.scalar_one_or_none.return_value = target
            call_order.append(mock_result)
        else:
            mock_result.scalar_one_or_none.return_value = None
            call_order.append(mock_result)
        return mock_result

    async def _scalar(stmt, *args, **kwargs):
        return admin_count

    db.execute = _execute
    db.scalar = _scalar
    return db


class TestPromoteAdmin:
    @pytest.mark.asyncio
    async def test_promote_sets_role_admin(self) -> None:
        """C6.1: POST promote-admin sets target.role = 'admin'."""
        from app.api.admin.users import promote_admin

        caller = _make_user("admin1", role="admin")
        target = _make_user("u2", role="member")
        org = _make_org()

        db = _make_db(caller, org, target=target)
        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            with patch("app.api.admin.users.emit_event", MagicMock()):
                creds = MagicMock()
                result = await promote_admin(
                    zitadel_user_id=target.zitadel_user_id,
                    credentials=creds,
                    db=db,
                )

        assert target.role == "admin"
        assert "promoted" in result.message.lower() or result.message

    @pytest.mark.asyncio
    async def test_promote_non_member_raises_404(self) -> None:
        """C6.1: Promoting a user not in the org raises 404."""
        from app.api.admin.users import promote_admin

        caller = _make_user("admin1", role="admin")
        org = _make_org()

        db = _make_db(caller, org, target=None)  # no target found
        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            creds = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await promote_admin(
                    zitadel_user_id="ghost",
                    credentials=creds,
                    db=db,
                )
        assert exc_info.value.status_code == 404


class TestDemoteAdmin:
    @pytest.mark.asyncio
    async def test_demote_sets_role_member(self) -> None:
        """C6.2: POST demote-admin sets target.role = 'member'."""
        from app.api.admin.users import demote_admin

        caller = _make_user("admin1", role="admin")
        target = _make_user("admin2", role="admin")
        org = _make_org()

        db = _make_db(caller, org, target=target, admin_count=2)
        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            with patch("app.api.admin.users.emit_event", MagicMock()):
                creds = MagicMock()
                result = await demote_admin(
                    zitadel_user_id=target.zitadel_user_id,
                    credentials=creds,
                    db=db,
                )

        assert target.role == "member"
        assert result.message

    @pytest.mark.asyncio
    async def test_demote_last_admin_raises_409(self) -> None:
        """C6.2: Demoting last admin raises HTTP 409 Conflict."""
        from app.api.admin.users import demote_admin

        caller = _make_user("admin1", role="admin")
        target = _make_user("admin1", role="admin")  # same person, only admin
        org = _make_org()

        db = _make_db(caller, org, target=target, admin_count=1)
        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            creds = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await demote_admin(
                    zitadel_user_id=target.zitadel_user_id,
                    credentials=creds,
                    db=db,
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_demote_non_admin_raises_400(self) -> None:
        """C6.2: Target must be admin; demoting a member raises HTTP 400."""
        from app.api.admin.users import demote_admin

        caller = _make_user("admin1", role="admin")
        target = _make_user("u2", role="member")
        org = _make_org()

        db = _make_db(caller, org, target=target, admin_count=2)
        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            creds = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await demote_admin(
                    zitadel_user_id=target.zitadel_user_id,
                    credentials=creds,
                    db=db,
                )
        assert exc_info.value.status_code == 400


class TestLeaveWorkspace:
    @pytest.mark.asyncio
    async def test_member_can_leave(self) -> None:
        """C6.3: A non-admin member can leave without restriction."""
        from app.api.admin.users import leave_workspace

        caller = _make_user("u1", role="member")
        org = _make_org()

        db = AsyncMock()
        db.add = MagicMock()
        db.delete = AsyncMock()

        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("u1", org, caller))):
            with patch("app.api.admin.users.emit_event", MagicMock()):
                creds = MagicMock()
                result = await leave_workspace(credentials=creds, db=db)

        db.delete.assert_awaited_once_with(caller)
        assert result.message

    @pytest.mark.asyncio
    async def test_last_admin_cannot_leave(self) -> None:
        """C6.3 + C6.7: Last admin leaving raises 409."""
        from app.api.admin.users import leave_workspace

        caller = _make_user("admin1", role="admin")
        org = _make_org()

        db = AsyncMock()
        db.add = MagicMock()
        db.delete = AsyncMock()

        async def _scalar(stmt, *args, **kwargs):
            return 1  # only one admin

        db.scalar = _scalar

        with patch("app.api.admin.users._get_caller_org", AsyncMock(return_value=("admin1", org, caller))):
            creds = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await leave_workspace(credentials=creds, db=db)

        assert exc_info.value.status_code == 409
