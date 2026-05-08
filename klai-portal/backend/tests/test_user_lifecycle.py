"""
Tests for SPEC-AUTH-001: User lifecycle endpoints (suspend, reactivate, offboard).

Pure unit tests -- no real DB, all async sessions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _mock_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    return org


def _mock_caller(role: str = "admin") -> MagicMock:
    caller = MagicMock()
    caller.role = role
    return caller


def _mock_user(status: str = "active", org_id: int = 1) -> MagicMock:
    user = MagicMock()
    user.status = status
    user.org_id = org_id
    user.zitadel_user_id = "user-1"
    return user


# ---------------------------------------------------------------------------
# Suspend
# ---------------------------------------------------------------------------


class TestSuspendUser:
    @pytest.mark.asyncio
    async def test_suspend_active_user_succeeds(self) -> None:
        from app.api.admin.users import suspend_user

        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        result = await suspend_user(
            zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
        )

        assert user.status == "suspended"
        assert "suspended" in result.message.lower()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suspend_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import suspend_user

        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await suspend_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_suspend_already_suspended_returns_409(self) -> None:
        from app.api.admin.users import suspend_user

        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await suspend_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Reactivate
# ---------------------------------------------------------------------------


class TestReactivateUser:
    @pytest.mark.asyncio
    async def test_reactivate_suspended_user_succeeds(self) -> None:
        from app.api.admin.users import reactivate_user

        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        result = await reactivate_user(
            zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
        )

        assert user.status == "active"
        assert "reactivated" in result.message.lower()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reactivate_active_user_returns_409(self) -> None:
        from app.api.admin.users import reactivate_user

        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await reactivate_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_reactivate_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import reactivate_user

        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await reactivate_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Offboard
# ---------------------------------------------------------------------------


class TestOffboardUser:
    @pytest.mark.asyncio
    async def test_offboard_active_user_cascade(self) -> None:
        """Offboard deletes memberships, calls Zitadel, sets status.

        SPEC-PORTAL-RBAC-001: portal_user_products is no longer written/deleted
        per user lifecycle event -- products derive from (role, plan,
        enabled_addons) at read time. The execute call count drops from 3 to 2.
        """
        from app.api.admin.users import offboard_user

        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        # First execute: user lookup
        # Second execute: delete memberships
        mock_db.execute.side_effect = [mock_result, MagicMock()]
        mock_zitadel = AsyncMock()

        with (
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
        ):
            mock_settings.zitadel_portal_org_id = "org-id"
            result = await offboard_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert user.status == "offboarded"
        assert "offboarded" in result.message
        mock_zitadel.deactivate_user.assert_awaited_once()
        # 2 execute calls: user lookup + delete memberships
        assert mock_db.execute.await_count == 2
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offboard_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import offboard_user

        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await offboard_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_offboard_suspended_user_succeeds(self) -> None:
        """Suspended users can be offboarded (terminal state from any non-offboarded state)."""
        from app.api.admin.users import offboard_user

        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.side_effect = [mock_result, MagicMock(), MagicMock()]
        mock_zitadel = AsyncMock()

        with (
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
        ):
            mock_settings.zitadel_portal_org_id = "org-id"
            await offboard_user(
                zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert user.status == "offboarded"

    @pytest.mark.asyncio
    async def test_offboard_user_not_found_returns_404(self) -> None:
        from app.api.admin.users import offboard_user

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc_info:
            await offboard_user(
                zitadel_user_id="user-999", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Memberships preserved after suspend
# ---------------------------------------------------------------------------


class TestSuspendPreservesMemberships:
    @pytest.mark.asyncio
    async def test_suspend_does_not_delete_memberships(self) -> None:
        """Suspending a user should NOT remove their group memberships."""
        from app.api.admin.users import suspend_user

        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        await suspend_user(
            zitadel_user_id="user-1", perms=make_perms(role="admin", user_id="admin-1", org_id=1), db=mock_db
        )

        # Only 1 execute call (user lookup), no delete calls
        assert mock_db.execute.await_count == 1
        mock_db.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TestRequireAtLeast:
    """SPEC-PORTAL-RBAC-001: _require_admin_or_group_admin removed; use _require_at_least."""

    def test_admin_passes(self) -> None:
        from app.core.profiles import _require_at_least

        caller = _mock_caller(role="admin")
        _require_at_least("group_manager")(caller_user=caller)  # no raise

    def test_group_manager_passes(self) -> None:
        from app.core.profiles import _require_at_least

        caller = _mock_caller(role="group_manager")
        _require_at_least("group_manager")(caller_user=caller)  # no raise

    def test_kb_manager_blocked_for_group_management(self) -> None:
        from app.core.profiles import _require_at_least

        caller = _mock_caller(role="kb_manager")
        with pytest.raises(HTTPException) as exc_info:
            _require_at_least("group_manager")(caller_user=caller)
        assert exc_info.value.status_code == 403

    def test_personal_blocked(self) -> None:
        from app.core.profiles import _require_at_least

        caller = _mock_caller(role="personal")
        with pytest.raises(HTTPException) as exc_info:
            _require_at_least("group_manager")(caller_user=caller)
        assert exc_info.value.status_code == 403
