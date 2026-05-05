"""SPEC-PORTAL-ADMIN-UI-001 backend safety tests.

REQ-2 unified change-profile endpoint: PATCH /api/admin/users/<id>/role.
This SPEC merges the legacy promote-admin / demote-admin paths into a single
endpoint. The min-1-admin invariant from POST /demote-admin must therefore
also live on PATCH /role; otherwise the new admin UI can demote the last
admin and lock a tenant out of its own workspace.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_user(uid: str = "u1", role: str = "company") -> MagicMock:
    user = MagicMock()
    user.zitadel_user_id = uid
    user.org_id = 1
    user.role = role
    user.status = "active"
    return user


def _make_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.name = "Acme"
    org.seats = 10
    return org


def _make_db(target: MagicMock | None, admin_count: int = 2) -> AsyncMock:
    """AsyncMock DB that returns `target` from every scalar_one_or_none and
    `admin_count` from scalar() (for the COUNT query). Sufficient because
    update_user_role only reads two results: the target lookup and the
    admin-count COUNT. The lock SELECT consumes a result the handler never
    inspects.
    """
    db = AsyncMock()
    db.add = MagicMock()

    async def _execute(_stmt, *_args, **_kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = target
        return result

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

        caller = _make_user("admin1", role="admin")
        target = _make_user("admin1", role="admin")  # same person, only admin
        org = _make_org()

        db = _make_db(target=target, admin_count=1)
        with patch(
            "app.api.admin.users._get_caller_org",
            AsyncMock(return_value=("admin1", org, caller)),
        ):
            creds = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await update_user_role(
                    zitadel_user_id=target.zitadel_user_id,
                    body=RoleUpdateRequest(role="company"),
                    credentials=creds,
                    db=db,
                )
        assert exc_info.value.status_code == 409
        # Target unchanged because the handler raised before assigning.
        assert target.role == "admin"

    @pytest.mark.asyncio
    async def test_demoting_admin_when_two_remain_succeeds(self) -> None:
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        caller = _make_user("admin1", role="admin")
        target = _make_user("admin2", role="admin")
        org = _make_org()

        db = _make_db(target=target, admin_count=2)
        with patch(
            "app.api.admin.users._get_caller_org",
            AsyncMock(return_value=("admin1", org, caller)),
        ):
            creds = MagicMock()
            result = await update_user_role(
                zitadel_user_id=target.zitadel_user_id,
                body=RoleUpdateRequest(role="company"),
                credentials=creds,
                db=db,
            )
        assert target.role == "company"
        assert result.message

    @pytest.mark.asyncio
    async def test_promoting_to_admin_skips_min_admin_check(self) -> None:
        """Promoting any user to admin must never touch the min-admin guard."""
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        caller = _make_user("admin1", role="admin")
        target = _make_user("u2", role="company")
        org = _make_org()

        # admin_count=1 here is irrelevant — we're promoting, not demoting.
        db = _make_db(target=target, admin_count=1)
        with patch(
            "app.api.admin.users._get_caller_org",
            AsyncMock(return_value=("admin1", org, caller)),
        ):
            creds = MagicMock()
            result = await update_user_role(
                zitadel_user_id=target.zitadel_user_id,
                body=RoleUpdateRequest(role="admin"),
                credentials=creds,
                db=db,
            )
        assert target.role == "admin"
        assert result.message

    @pytest.mark.asyncio
    async def test_changing_non_admin_to_other_non_admin_succeeds(self) -> None:
        """A company → kb_manager move should never trip the admin guard."""
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        caller = _make_user("admin1", role="admin")
        target = _make_user("u2", role="company")
        org = _make_org()

        db = _make_db(target=target, admin_count=1)
        with patch(
            "app.api.admin.users._get_caller_org",
            AsyncMock(return_value=("admin1", org, caller)),
        ):
            creds = MagicMock()
            result = await update_user_role(
                zitadel_user_id=target.zitadel_user_id,
                body=RoleUpdateRequest(role="kb_manager"),
                credentials=creds,
                db=db,
            )
        assert target.role == "kb_manager"
        assert result.message
