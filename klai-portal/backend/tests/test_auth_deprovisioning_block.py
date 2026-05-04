"""SPEC-INFRA-TENANT-DELETE-001 — _get_caller_org deprovisioning guard.

Verifies that _get_caller_org raises HTTP 403 (tenant_deleting) when the
org's provisioning_status is 'deprovisioning', and that passing
allow_during_deprovisioning=True bypasses the guard so the status-polling
endpoint can still respond.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from helpers import FakeResult, setup_db

from app.api.admin import _get_caller_org

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_credentials(token: str = "tok") -> MagicMock:
    creds = MagicMock()
    creds.credentials = token
    return creds


def _make_org(provisioning_status: str = "ready") -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.provisioning_status = provisioning_status
    return org


def _make_user(role: str = "admin") -> MagicMock:
    user = MagicMock()
    user.role = role
    user.zitadel_user_id = "zit-user-1"
    return user


def _make_db(org: MagicMock, user: MagicMock) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    row = (org, user)
    setup_db(db, [FakeResult(rows=[row])])
    return db


# ---------------------------------------------------------------------------
# Happy-path — org not deprovisioning
# ---------------------------------------------------------------------------


class TestGetCallerOrgHappyPath:
    """_get_caller_org succeeds when provisioning_status is not 'deprovisioning'."""

    @pytest.mark.asyncio
    async def test_returns_tuple_on_ready_org(self):
        org = _make_org("ready")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", AsyncMock()),
        ):
            result = await _get_caller_org(creds, db)

        assert result[0] == "zit-user-1"
        assert result[1] is org
        assert result[2] is user

    @pytest.mark.asyncio
    async def test_returns_tuple_on_failed_deprovisioning_org(self):
        """failed_deprovisioning orgs are NOT blocked — retry endpoint needs access."""
        org = _make_org("failed_deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", AsyncMock()),
        ):
            result = await _get_caller_org(creds, db)

        assert result[1] is org


# ---------------------------------------------------------------------------
# 403 guard — deprovisioning in progress
# ---------------------------------------------------------------------------


class TestGetCallerOrgDeprovisioningBlock:
    """_get_caller_org raises 403 tenant_deleting while provisioning_status == 'deprovisioning'."""

    @pytest.mark.asyncio
    async def test_raises_403_when_deprovisioning(self):
        org = _make_org("deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _get_caller_org(creds, db)

        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == "tenant_deleting"

    @pytest.mark.asyncio
    async def test_set_tenant_not_called_when_blocked(self):
        """set_tenant must NOT be called before raising the 403 guard."""
        org = _make_org("deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()
        mock_set_tenant = AsyncMock()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", mock_set_tenant),
        ):
            with pytest.raises(HTTPException):
                await _get_caller_org(creds, db)

        mock_set_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# allow_during_deprovisioning=True bypass
# ---------------------------------------------------------------------------


class TestGetCallerOrgAllowDuringDeprovisioning:
    """allow_during_deprovisioning=True bypasses the 403 guard."""

    @pytest.mark.asyncio
    async def test_bypass_returns_tuple_while_deprovisioning(self):
        org = _make_org("deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", AsyncMock()),
        ):
            result = await _get_caller_org(creds, db, allow_during_deprovisioning=True)

        assert result[1] is org

    @pytest.mark.asyncio
    async def test_bypass_calls_set_tenant(self):
        org = _make_org("deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()
        mock_set_tenant = AsyncMock()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", mock_set_tenant),
        ):
            await _get_caller_org(creds, db, allow_during_deprovisioning=True)

        mock_set_tenant.assert_called_once_with(db, org.id)

    @pytest.mark.asyncio
    async def test_bypass_false_still_blocks(self):
        """Explicit False is same as default — still blocks."""
        org = _make_org("deprovisioning")
        user = _make_user()
        db = _make_db(org, user)
        creds = _make_credentials()

        with (
            patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})),
            patch("app.api.admin.set_tenant", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _get_caller_org(creds, db, allow_during_deprovisioning=False)

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Pre-existing auth error paths are unaffected
# ---------------------------------------------------------------------------


class TestGetCallerOrgAuthErrors:
    """Existing 401/404 paths are not disturbed by the deprovisioning change."""

    @pytest.mark.asyncio
    async def test_401_on_userinfo_failure(self):
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch("app.api.admin.zitadel.get_userinfo", AsyncMock(side_effect=RuntimeError("network"))):
            with pytest.raises(HTTPException) as exc_info:
                await _get_caller_org(creds, db)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_404_when_no_org_row(self):
        db = AsyncMock()
        db.add = MagicMock()
        setup_db(db, [FakeResult(rows=[])])
        creds = _make_credentials()

        with patch("app.api.admin.zitadel.get_userinfo", AsyncMock(return_value={"sub": "zit-user-1"})):
            with pytest.raises(HTTPException) as exc_info:
                await _get_caller_org(creds, db)

        assert exc_info.value.status_code == 404
