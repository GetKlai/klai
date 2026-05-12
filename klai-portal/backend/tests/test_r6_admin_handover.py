"""
R6 tests -- admin handover: promote-admin, demote-admin, DELETE /api/admin/users/me
(SPEC-AUTH-009 R6 + C6.1/C6.2/C6.3/C6.7).

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2a: endpoints take ``perms: UserPermissions``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _make_user(uid: str = "u1", role: str = "company") -> MagicMock:
    u = MagicMock()
    u.zitadel_user_id = uid
    u.org_id = 1
    u.role = role
    u.status = "active"
    return u


def _make_db(target: MagicMock | None = None, admin_count: int = 2, plan: str = "knowledge") -> AsyncMock:
    """Return an AsyncMock DB shaped for ``promote_admin`` / ``demote_admin``:

    - First ``execute()`` is the locked-org SELECT...FOR UPDATE (added by
      Phase 3 fix #2 so plan-ceiling reads the locked plan, not the
      ``perms.plan`` request-start snapshot). Returns a synthetic
      PortalOrg whose ``plan`` is configurable (defaults to ``complete``).
    - Subsequent ``execute()`` calls are the target-user lookup; their
      result's ``scalar_one_or_none()`` returns ``target``.
    - ``scalar()`` returns ``admin_count`` (admin-count COUNT query).
    """
    db = AsyncMock()
    db.add = MagicMock()

    locked_org = MagicMock()
    locked_org.plan = plan

    locked_result = MagicMock()
    locked_result.scalar_one.return_value = locked_org

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = target

    call_count = {"n": 0}

    async def _execute(stmt, *args, **kwargs):
        call_count["n"] += 1
        return locked_result if call_count["n"] == 1 else user_result

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

        target = _make_user("u2", role="company")
        db = _make_db(target=target)
        with patch("app.api.admin.users.emit_event", MagicMock()):
            result = await promote_admin(
                zitadel_user_id=target.zitadel_user_id,
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )

        assert target.role == "admin"
        assert "promoted" in result.message.lower() or result.message

    @pytest.mark.asyncio
    async def test_promote_non_member_raises_404(self) -> None:
        """C6.1: Promoting a user not in the org raises 404."""
        from app.api.admin.users import promote_admin

        db = _make_db(target=None)
        with pytest.raises(HTTPException) as exc_info:
            await promote_admin(
                zitadel_user_id="ghost",
                perms=make_perms(role="admin"),
                db=db,
            )
        assert exc_info.value.status_code == 404


class TestDemoteAdmin:
    @pytest.mark.asyncio
    async def test_demote_sets_role_company(self) -> None:
        """C6.2: POST demote-admin sets target.role = 'company'."""
        from app.api.admin.users import demote_admin

        target = _make_user("admin2", role="admin")
        db = _make_db(target=target, admin_count=2)
        with patch("app.api.admin.users.emit_event", MagicMock()):
            result = await demote_admin(
                zitadel_user_id=target.zitadel_user_id,
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )

        # Post profile-ladder migration: demoted admin lands on "company"
        # rung (formerly "member"). See SPEC-PORTAL-PROFILES-001.
        assert target.role == "company"
        assert result.message

    @pytest.mark.asyncio
    async def test_demote_last_admin_raises_409(self) -> None:
        """C6.2: Demoting last admin raises HTTP 409 Conflict."""
        from app.api.admin.users import demote_admin

        target = _make_user("admin1", role="admin")  # only admin
        db = _make_db(target=target, admin_count=1)
        with pytest.raises(HTTPException) as exc_info:
            await demote_admin(
                zitadel_user_id=target.zitadel_user_id,
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_demote_non_admin_raises_400(self) -> None:
        """C6.2: Target must be admin; demoting a member raises HTTP 400."""
        from app.api.admin.users import demote_admin

        target = _make_user("u2", role="company")
        db = _make_db(target=target, admin_count=2)
        with pytest.raises(HTTPException) as exc_info:
            await demote_admin(
                zitadel_user_id=target.zitadel_user_id,
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )
        assert exc_info.value.status_code == 400


class TestLeaveWorkspace:
    @pytest.mark.asyncio
    async def test_member_can_leave(self) -> None:
        """C6.3: A non-admin member can leave without restriction."""
        from app.api.admin.users import leave_workspace

        caller = _make_user("u1", role="company")
        db = AsyncMock()
        db.add = MagicMock()
        db.delete = AsyncMock()

        # leave_workspace re-fetches the caller's PortalUser ORM row for
        # `db.delete`. Mock the SELECT result to return the caller mock.
        async def _execute(stmt, *args, **kwargs):
            mock_result = MagicMock()
            mock_result.scalar_one.return_value = caller
            return mock_result

        db.execute = _execute

        with patch("app.api.admin.users.emit_event", MagicMock()):
            result = await leave_workspace(
                perms=make_perms(role="company", user_id="u1"),
                db=db,
            )

        db.delete.assert_awaited_once_with(caller)
        assert result.message

    @pytest.mark.asyncio
    async def test_last_admin_cannot_leave(self) -> None:
        """C6.3 + C6.7: Last admin leaving raises 409."""
        from app.api.admin.users import leave_workspace

        db = AsyncMock()
        db.add = MagicMock()
        db.delete = AsyncMock()

        async def _execute(stmt, *args, **kwargs):
            return MagicMock()

        async def _scalar(stmt, *args, **kwargs):
            return 1  # only one admin

        db.execute = _execute
        db.scalar = _scalar

        with pytest.raises(HTTPException) as exc_info:
            await leave_workspace(
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )

        assert exc_info.value.status_code == 409
