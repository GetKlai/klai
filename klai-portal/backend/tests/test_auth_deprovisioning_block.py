"""SPEC-INFRA-TENANT-DELETE-001 — deprovisioning guard on caller resolution.

Verifies that ``get_caller`` raises HTTP 403 (``tenant_deleting``) when the
org's ``provisioning_status`` is ``'deprovisioning'``, and that the variant
``get_caller_during_deprovisioning`` bypasses the guard so the
status-polling endpoint can still respond.

After SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2-cleanup the legacy
``_get_caller_org`` (and its ``allow_during_deprovisioning`` kwarg) is
gone; the equivalent split lives in ``app.core.permissions`` as two
public dependencies sharing ``_resolve_caller_with_options`` underneath.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.permissions import (
    UserPermissions,
    get_caller,
    get_caller_during_deprovisioning,
)
from app.core.profiles import ProfileRole

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_credentials(token: str = "tok") -> MagicMock:
    creds = MagicMock()
    creds.credentials = token
    return creds


def _make_perms(provisioning_status: str = "active") -> UserPermissions:
    """Synthetic ``UserPermissions`` with the requested provisioning status.

    The ``provisioning_status`` field is the only attribute the
    ``_resolve_caller_with_options`` deprovisioning guard inspects after
    ``resolve_user_permissions`` returns.
    """
    return UserPermissions(
        user_id="zit-user-1",
        org_id=1,
        org_slug="voys",
        role=ProfileRole.ADMIN,
        plan="knowledge",
        enabled_addons=frozenset(),
        platform_unlocked_features=frozenset(),
        effective_role=ProfileRole.ADMIN,
        effective_capabilities=frozenset(),
        effective_products=frozenset(),
        effective_kb_limits=None,  # type: ignore[arg-type]  # not asserted by guard
        is_platform_admin=False,
        provisioning_status=provisioning_status,
    )


# ---------------------------------------------------------------------------
# Happy path — org not deprovisioning
# ---------------------------------------------------------------------------


class TestGetCallerHappyPath:
    """``get_caller`` succeeds when ``provisioning_status`` is not 'deprovisioning'."""

    @pytest.mark.asyncio
    async def test_returns_perms_on_active_org(self):
        perms = _make_perms("active")
        db = AsyncMock()
        creds = _make_credentials()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", AsyncMock()),
        ):
            result = await get_caller(creds, db)

        assert result is perms

    @pytest.mark.asyncio
    async def test_returns_perms_on_failed_deprovisioning_org(self):
        """failed_deprovisioning orgs are NOT blocked — retry endpoint needs access."""
        perms = _make_perms("failed_deprovisioning")
        db = AsyncMock()
        creds = _make_credentials()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", AsyncMock()),
        ):
            result = await get_caller(creds, db)

        assert result is perms


# ---------------------------------------------------------------------------
# 403 guard — deprovisioning in progress
# ---------------------------------------------------------------------------


class TestGetCallerDeprovisioningBlock:
    """``get_caller`` raises 403 ``tenant_deleting`` while ``provisioning_status == 'deprovisioning'``."""

    @pytest.mark.asyncio
    async def test_raises_403_when_deprovisioning(self):
        perms = _make_perms("deprovisioning")
        db = AsyncMock()
        creds = _make_credentials()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_caller(creds, db)

        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == "tenant_deleting"

    @pytest.mark.asyncio
    async def test_set_tenant_not_called_when_blocked(self):
        """``set_tenant`` must NOT be called before raising the 403 guard."""
        perms = _make_perms("deprovisioning")
        db = AsyncMock()
        creds = _make_credentials()
        mock_set_tenant = AsyncMock()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", mock_set_tenant),
        ):
            with pytest.raises(HTTPException):
                await get_caller(creds, db)

        mock_set_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# ``get_caller_during_deprovisioning`` bypass
# ---------------------------------------------------------------------------


class TestGetCallerDuringDeprovisioning:
    """The bypass variant skips the 403 guard."""

    @pytest.mark.asyncio
    async def test_bypass_returns_perms_while_deprovisioning(self):
        perms = _make_perms("deprovisioning")
        db = AsyncMock()
        creds = _make_credentials()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", AsyncMock()),
        ):
            result = await get_caller_during_deprovisioning(creds, db)

        assert result is perms

    @pytest.mark.asyncio
    async def test_bypass_calls_set_tenant(self):
        perms = _make_perms("deprovisioning")
        db = AsyncMock()
        creds = _make_credentials()
        mock_set_tenant = AsyncMock()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=perms)),
            patch("app.core.permissions.set_tenant", mock_set_tenant),
        ):
            await get_caller_during_deprovisioning(creds, db)

        mock_set_tenant.assert_called_once_with(db, perms.org_id)


# ---------------------------------------------------------------------------
# Pre-existing auth error paths are unaffected
# ---------------------------------------------------------------------------


class TestGetCallerAuthErrors:
    """Existing 401/404 paths survive the rename."""

    @pytest.mark.asyncio
    async def test_401_on_userinfo_failure(self):
        db = AsyncMock()
        creds = _make_credentials()

        with patch(
            "app.core.permissions.zitadel.get_userinfo",
            AsyncMock(side_effect=RuntimeError("network")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_caller(creds, db)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_404_when_no_org_row(self):
        db = AsyncMock()
        creds = _make_credentials()

        with (
            patch("app.core.permissions.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.core.permissions.resolve_user_permissions", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_caller(creds, db)

        assert exc_info.value.status_code == 404
