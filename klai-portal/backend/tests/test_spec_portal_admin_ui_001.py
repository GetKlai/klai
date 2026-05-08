"""SPEC-PORTAL-ADMIN-UI-001 backend safety tests.

REQ-2 unified change-profile endpoint: PATCH /api/admin/users/<id>/role.
This SPEC merges the legacy promote-admin / demote-admin paths into a single
endpoint. The min-1-admin invariant from POST /demote-admin must therefore
also live on PATCH /role; otherwise the new admin UI can demote the last
admin and lock a tenant out of its own workspace.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2a: endpoint takes ``perms``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _make_user(uid: str = "u1", role: str = "company") -> MagicMock:
    user = MagicMock()
    user.zitadel_user_id = uid
    user.org_id = 1
    user.role = role
    user.status = "active"
    return user


def _make_db(target: MagicMock | None, admin_count: int = 2, plan: str = "complete") -> AsyncMock:
    """AsyncMock DB used by ``update_user_role``.

    Returns three things in order across the two ``db.execute`` calls + one
    ``db.scalar`` call the handler issues:

    1. First ``db.execute`` (locked org SELECT...FOR UPDATE) yields a result
       whose ``scalar_one()`` is a synthetic ``PortalOrg`` carrying ``plan``.
       This is the row read by ``assert_role_allowed_for_plan`` after Phase
       3 fix #2 (plan-from-locked-row, not from ``perms.plan`` snapshot).
    2. Second ``db.execute`` (user lookup) yields a result whose
       ``scalar_one_or_none()`` is ``target``.
    3. ``db.scalar`` (admin-count COUNT) returns ``admin_count``.

    Plan defaults to ``complete`` so the role-ceiling never blocks the
    test path; specify a more restrictive plan to exercise the gate.
    """
    db = AsyncMock()
    db.add = MagicMock()

    locked_org = MagicMock()
    locked_org.plan = plan

    locked_result = MagicMock()
    locked_result.scalar_one.return_value = locked_org

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = target

    # First execute -> locked org. Second + later -> target user lookup.
    call_count = {"n": 0}

    async def _execute(_stmt, *_args, **_kwargs):
        call_count["n"] += 1
        return locked_result if call_count["n"] == 1 else user_result

    async def _scalar(_stmt, *_args, **_kwargs):
        return admin_count

    db.execute = _execute
    db.scalar = _scalar
    return db


class TestUpdateUserRoleMinAdminInvariant:
    """REQ-2 safety extension: PATCH /role must refuse to demote the last admin."""

    @pytest.mark.asyncio
    async def test_demoting_last_admin_to_company_raises_409(self) -> None:
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        target = _make_user("admin1", role="admin")  # only admin
        db = _make_db(target=target, admin_count=1)
        with pytest.raises(HTTPException) as exc_info:
            await update_user_role(
                zitadel_user_id=target.zitadel_user_id,
                body=RoleUpdateRequest(role="company"),
                perms=make_perms(role="admin", user_id="admin1"),
                db=db,
            )
        assert exc_info.value.status_code == 409
        # Target unchanged because the handler raised before assigning.
        assert target.role == "admin"

    @pytest.mark.asyncio
    async def test_demoting_admin_when_two_remain_succeeds(self) -> None:
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        target = _make_user("admin2", role="admin")
        db = _make_db(target=target, admin_count=2)
        result = await update_user_role(
            zitadel_user_id=target.zitadel_user_id,
            body=RoleUpdateRequest(role="company"),
            perms=make_perms(role="admin", user_id="admin1"),
            db=db,
        )
        assert target.role == "company"
        assert result.message

    @pytest.mark.asyncio
    async def test_promoting_to_admin_skips_min_admin_check(self) -> None:
        """Promoting any user to admin must never touch the min-admin guard."""
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        target = _make_user("u2", role="company")
        # admin_count=1 here is irrelevant — we're promoting, not demoting.
        db = _make_db(target=target, admin_count=1)
        result = await update_user_role(
            zitadel_user_id=target.zitadel_user_id,
            body=RoleUpdateRequest(role="admin"),
            perms=make_perms(role="admin", user_id="admin1"),
            db=db,
        )
        assert target.role == "admin"
        assert result.message

    @pytest.mark.asyncio
    async def test_changing_non_admin_to_other_non_admin_succeeds(self) -> None:
        """A company -> kb_manager move should never trip the admin guard."""
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        target = _make_user("u2", role="company")
        db = _make_db(target=target, admin_count=1)
        result = await update_user_role(
            zitadel_user_id=target.zitadel_user_id,
            body=RoleUpdateRequest(role="kb_manager"),
            perms=make_perms(role="admin", user_id="admin1"),
            db=db,
        )
        assert target.role == "kb_manager"
        assert result.message
