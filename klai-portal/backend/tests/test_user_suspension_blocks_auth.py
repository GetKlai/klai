"""REQ-12 (Finding A-6, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): suspended
users must be denied authentication AND the Zitadel identity must be
locked / unlocked in lockstep with portal_users.status changes.

AC12.1 — suspended user request returns 403 user_suspended (not 401)
AC12.2 — platform_suspend triggers zitadel.lock_user after the DB commit
AC12.3 — platform_reactivate triggers zitadel.unlock_user after the DB commit
AC12.4 — Zitadel lock/unlock failure surfaces zitadel_sync_failed=true and
         emits a platform_admin.suspend_zitadel_desync (or .reactivate_zitadel_desync)
         audit event WITHOUT rolling back the DB change.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from tests.conftest import make_perms


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _platform_perms():
    return make_perms(
        role="admin",
        user_id="platform-admin",
        org_id=1,
        org_slug="getklai",
        is_platform_admin=True,
    )


# ---------------------------------------------------------------------------
# AC12.1 — suspended user gets 403 at the resolver
# ---------------------------------------------------------------------------


class TestSuspendedUserBlockedAtAuth:
    """_resolve_caller_with_options MUST refuse callers whose
    portal_users.status is 'suspended', regardless of token validity."""

    @pytest.mark.asyncio
    async def test_suspended_user_request_returns_403_user_suspended(self) -> None:
        from app.core import permissions as perms_module

        suspended_perms = make_perms(status="suspended")

        # Bypass the real Zitadel + DB by stubbing the resolver to return our
        # suspended UserPermissions directly.
        with (
            patch.object(perms_module.zitadel, "get_userinfo", new=AsyncMock(return_value={"sub": "u-1"})),
            patch.object(perms_module, "resolve_user_permissions", new=AsyncMock(return_value=suspended_perms)),
        ):
            from fastapi.security import HTTPAuthorizationCredentials

            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
            db = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await perms_module._resolve_caller_with_options(creds, db, allow_during_deprovisioning=False)

        assert exc.value.status_code == status.HTTP_403_FORBIDDEN
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail.get("error_code") == "user_suspended"

    @pytest.mark.asyncio
    async def test_active_user_passes_through(self) -> None:
        from app.core import permissions as perms_module

        active_perms = make_perms(status="active")

        with (
            patch.object(perms_module.zitadel, "get_userinfo", new=AsyncMock(return_value={"sub": "u-1"})),
            patch.object(perms_module, "resolve_user_permissions", new=AsyncMock(return_value=active_perms)),
            patch.object(perms_module, "set_tenant", new=AsyncMock()),
        ):
            from fastapi.security import HTTPAuthorizationCredentials

            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
            db = AsyncMock()
            result = await perms_module._resolve_caller_with_options(creds, db, allow_during_deprovisioning=False)

        assert result is active_perms


# ---------------------------------------------------------------------------
# AC12.2 / AC12.3 / AC12.4 — Zitadel lock / unlock sync
# ---------------------------------------------------------------------------


def _user(*, status_: str = "active"):
    u = MagicMock()
    u.zitadel_user_id = "target-user"
    u.org_id = 42
    u.role = "company"
    u.status = status_
    return u


class TestPlatformSuspendZitadelLock:
    @pytest.mark.asyncio
    async def test_platform_suspend_calls_zitadel_lock_user_after_commit(self) -> None:
        from app.api.admin.platform_manage import platform_suspend

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: _user()))
        with (
            patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
            patch("app.api.admin.platform_manage.fire_role_change_notification"),
            patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
            patch(
                "app.api.admin.platform_manage.get_user_global_membership_state",
                new=AsyncMock(return_value=SimpleNamespace(active_count=0)),
            ),
            patch("app.api.admin.platform_manage.zitadel") as zitadel,
        ):
            zitadel.lock_user = AsyncMock()

            await platform_suspend(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

        db.commit.assert_awaited_once()
        zitadel.lock_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_platform_suspend_skips_zitadel_lock_when_other_active_membership_exists(self) -> None:
        from app.api.admin.platform_manage import platform_suspend

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: _user()))
        with (
            patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
            patch("app.api.admin.platform_manage.fire_role_change_notification"),
            patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
            patch(
                "app.api.admin.platform_manage.get_user_global_membership_state",
                new=AsyncMock(return_value=SimpleNamespace(active_count=1)),
            ),
            patch("app.api.admin.platform_manage.zitadel") as zitadel,
        ):
            zitadel.lock_user = AsyncMock()

            response = await platform_suspend(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

        db.commit.assert_awaited_once()
        zitadel.lock_user.assert_not_awaited()
        assert response.zitadel_sync_failed is False

    @pytest.mark.asyncio
    async def test_platform_suspend_zitadel_lock_failure_surfaces_desync(self) -> None:
        """AC12.4 — Zitadel lock failure: DB stays committed, desync audit emitted."""
        from app.api.admin.platform_manage import platform_suspend

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: _user()))
        log_calls: list[dict] = []

        async def _capture(**kw):
            log_calls.append(kw)

        with (
            patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
            patch("app.api.admin.platform_manage.fire_role_change_notification"),
            patch("app.api.admin.platform_manage.log_event", side_effect=_capture),
            patch(
                "app.api.admin.platform_manage.get_user_global_membership_state",
                new=AsyncMock(return_value=SimpleNamespace(active_count=0)),
            ),
            patch("app.api.admin.platform_manage.zitadel") as zitadel,
        ):
            zitadel.lock_user = AsyncMock(side_effect=RuntimeError("zitadel 502"))

            response = await platform_suspend(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

        db.commit.assert_awaited_once()  # NO rollback on Zitadel failure
        actions = [c["action"] for c in log_calls]
        assert "platform_admin.suspend_zitadel_desync" in actions
        # response surfaces the desync flag so the operator can act
        assert getattr(response, "zitadel_sync_failed", False) is True


class TestPlatformReactivateZitadelUnlock:
    @pytest.mark.asyncio
    async def test_platform_reactivate_calls_zitadel_unlock_user_after_commit(self) -> None:
        from app.api.admin.platform_manage import platform_reactivate

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: _user(status_="suspended")))
        with (
            patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
            patch("app.api.admin.platform_manage.fire_role_change_notification"),
            patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
            patch(
                "app.api.admin.platform_manage.get_user_global_membership_state",
                new=AsyncMock(return_value=SimpleNamespace(active_count=1)),
            ),
            patch("app.api.admin.platform_manage.zitadel") as zitadel,
        ):
            zitadel.unlock_user = AsyncMock()

            await platform_reactivate(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

        db.commit.assert_awaited_once()
        zitadel.unlock_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_platform_reactivate_zitadel_unlock_failure_surfaces_desync(self) -> None:
        from app.api.admin.platform_manage import platform_reactivate

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: _user(status_="suspended")))
        log_calls: list[dict] = []

        async def _capture(**kw):
            log_calls.append(kw)

        with (
            patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
            patch("app.api.admin.platform_manage.fire_role_change_notification"),
            patch("app.api.admin.platform_manage.log_event", side_effect=_capture),
            patch(
                "app.api.admin.platform_manage.get_user_global_membership_state",
                new=AsyncMock(return_value=SimpleNamespace(active_count=1)),
            ),
            patch("app.api.admin.platform_manage.zitadel") as zitadel,
        ):
            zitadel.unlock_user = AsyncMock(side_effect=RuntimeError("zitadel 502"))

            response = await platform_reactivate(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

        db.commit.assert_awaited_once()
        actions = [c["action"] for c in log_calls]
        assert "platform_admin.reactivate_zitadel_desync" in actions
        assert getattr(response, "zitadel_sync_failed", False) is True
