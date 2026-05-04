"""SPEC-PORTAL-RBAC-001: tenant-level add-on toggle endpoints.

After RBAC-001, the add-on toggles are the *only* state needed -- no
group-membership / per-user-product side-effects on update_addons. These
tests cover the get + patch endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org(enabled_addons: list[str] | None = None, plan: str = "core") -> MagicMock:
    org = MagicMock()
    org.id = 42
    org.plan = plan
    org.enabled_addons = enabled_addons or []
    return org


def _mock_caller(role: str = "admin") -> MagicMock:
    caller = MagicMock()
    caller.role = role
    return caller


class TestGetAddons:
    @pytest.mark.asyncio
    async def test_returns_current_state(self) -> None:
        from app.api.admin.settings import get_addons

        org = _mock_org(enabled_addons=["scribe"])
        caller = _mock_caller()
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            result = await get_addons(credentials=MagicMock(), db=AsyncMock())
        assert result.enabled_addons == ["scribe"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_by_default(self) -> None:
        from app.api.admin.settings import get_addons

        org = _mock_org(enabled_addons=[])
        caller = _mock_caller()
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            result = await get_addons(credentials=MagicMock(), db=AsyncMock())
        assert result.enabled_addons == []


class TestUpdateAddons:
    @pytest.mark.asyncio
    async def test_patch_valid_addon_updates_org(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=[])
        caller = _mock_caller()
        mock_db = AsyncMock()
        body = AddonsUpdate(enabled_addons=["scribe"])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event") as mock_emit,
        ):
            result = await update_addons(body=body, credentials=MagicMock(), db=mock_db)
        assert result.enabled_addons == ["scribe"]
        assert org.enabled_addons == ["scribe"]
        mock_db.commit.assert_called_once()
        assert mock_emit.call_args.args[0] == "tenant.addons_updated"

    @pytest.mark.asyncio
    async def test_patch_unknown_addon_returns_400(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller()
        body = AddonsUpdate(enabled_addons=["hacker_product"])
        with patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await update_addons(body=body, credentials=MagicMock(), db=AsyncMock())
        assert exc_info.value.status_code == 400
        assert "hacker_product" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_patch_both_addons(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller()
        body = AddonsUpdate(enabled_addons=["scribe", "docs"])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, credentials=MagicMock(), db=AsyncMock())
        assert set(result.enabled_addons) == {"scribe", "docs"}

    @pytest.mark.asyncio
    async def test_patch_empty_disables_all(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=["scribe"])
        caller = _mock_caller()
        body = AddonsUpdate(enabled_addons=[])
        with (
            patch("app.api.admin.settings._get_caller_org", return_value=("admin-1", org, caller)),
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, credentials=MagicMock(), db=AsyncMock())
        assert result.enabled_addons == []

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        caller = _mock_caller(role="company")
        body = AddonsUpdate(enabled_addons=["scribe"])
        with patch("app.api.admin.settings._get_caller_org", return_value=("user-1", org, caller)):
            with pytest.raises(HTTPException) as exc_info:
                await update_addons(body=body, credentials=MagicMock(), db=AsyncMock())
        assert exc_info.value.status_code == 403
