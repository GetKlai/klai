"""
Tests for SPEC-AUTH-001: User lifecycle endpoints (suspend, reactivate, offboard).

Pure unit tests -- no real DB, all async sessions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


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

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            result = await suspend_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert user.status == "suspended"
        assert "suspended" in result.message.lower()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suspend_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import suspend_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await suspend_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_suspend_already_suspended_returns_409(self) -> None:
        from app.api.admin.users import suspend_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await suspend_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Reactivate
# ---------------------------------------------------------------------------


class TestReactivateUser:
    @pytest.mark.asyncio
    async def test_reactivate_suspended_user_succeeds(self) -> None:
        from app.api.admin.users import reactivate_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            result = await reactivate_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert user.status == "active"
        assert "reactivated" in result.message.lower()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reactivate_active_user_returns_409(self) -> None:
        from app.api.admin.users import reactivate_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await reactivate_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_reactivate_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import reactivate_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await reactivate_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Offboard
# ---------------------------------------------------------------------------


class TestOffboardUser:
    @pytest.mark.asyncio
    async def test_offboard_active_user_cascade(self) -> None:
        """Offboard deletes memberships, products, calls Zitadel, sets status."""
        from app.api.admin.users import offboard_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        # First execute: user lookup
        # Second execute: delete memberships
        # Third execute: delete products
        mock_db.execute.side_effect = [mock_result, MagicMock(), MagicMock()]
        mock_credentials = MagicMock()

        mock_zitadel = AsyncMock()

        with (
            patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
        ):
            mock_settings.zitadel_portal_org_id = "org-id"
            result = await offboard_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert user.status == "offboarded"
        assert "offboarded" in result.message
        mock_zitadel.deactivate_user.assert_awaited_once()
        # 3 execute calls: user lookup + delete memberships + delete products
        assert mock_db.execute.await_count == 3
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offboard_offboarded_user_returns_409(self) -> None:
        from app.api.admin.users import offboard_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="offboarded")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await offboard_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_offboard_suspended_user_succeeds(self) -> None:
        """Suspended users can be offboarded (terminal state from any non-offboarded state)."""
        from app.api.admin.users import offboard_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="suspended")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.side_effect = [mock_result, MagicMock(), MagicMock()]
        mock_credentials = MagicMock()

        mock_zitadel = AsyncMock()

        with (
            patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
        ):
            mock_settings.zitadel_portal_org_id = "org-id"
            await offboard_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        assert user.status == "offboarded"

    @pytest.mark.asyncio
    async def test_offboard_user_not_found_returns_404(self) -> None:
        from app.api.admin.users import offboard_user

        org = _mock_org()
        caller = _mock_caller()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await offboard_user(zitadel_user_id="user-999", credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Memberships preserved after suspend
# ---------------------------------------------------------------------------


class TestSuspendPreservesMemberships:
    @pytest.mark.asyncio
    async def test_suspend_does_not_delete_memberships(self) -> None:
        """Suspending a user should NOT remove their group memberships."""
        from app.api.admin.users import suspend_user

        org = _mock_org()
        caller = _mock_caller()
        user = _mock_user(status="active")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result
        mock_credentials = MagicMock()

        with patch("app.api.admin.users._get_caller_org", return_value=("admin-1", org, caller)):
            await suspend_user(zitadel_user_id="user-1", credentials=mock_credentials, db=mock_db)

        # Only 1 execute call (user lookup), no delete calls
        assert mock_db.execute.await_count == 1
        mock_db.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TestRequireAdminOrGroupAdmin:
    @pytest.mark.asyncio
    async def test_admin_passes(self) -> None:
        from app.api.dependencies import _require_admin_or_group_admin

        caller = _mock_caller(role="admin")
        mock_db = AsyncMock()

        # Should not raise
        await _require_admin_or_group_admin(group_id=1, caller_user=caller, db=mock_db)

    @pytest.mark.asyncio
    async def test_group_admin_passes(self) -> None:
        from app.api.dependencies import _require_admin_or_group_admin

        caller = _mock_caller(role="group-admin")
        mock_db = AsyncMock()

        # system_key check returns None (not a system group)
        system_key_result = MagicMock()
        system_key_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = system_key_result

        # Should not raise
        await _require_admin_or_group_admin(group_id=1, caller_user=caller, db=mock_db)

    @pytest.mark.asyncio
    async def test_non_admin_non_group_admin_raises_403(self) -> None:
        from app.api.dependencies import _require_admin_or_group_admin

        caller = _mock_caller(role="member")
        mock_db = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await _require_admin_or_group_admin(group_id=1, caller_user=caller, db=mock_db)

        assert exc_info.value.status_code == 403
