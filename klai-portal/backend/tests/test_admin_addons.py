"""SPEC-PORTAL-RBAC-001: tenant-level add-on toggle endpoints.

After RBAC-001, the add-on toggles are the *only* state needed -- no
group-membership / per-user-product side-effects on update_addons. These
tests cover the get + patch endpoints.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2a: endpoints now take
``perms: UserPermissions`` directly (no more ``_get_caller_org`` patch).
The rol-gate (admin-only) is enforced declaratively by
``Depends(get_caller_at_least(ProfileRole.ADMIN))`` — that branch is
covered in `test_permissions.py::test_get_caller_at_least_role_matrix`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _mock_org(enabled_addons: list[str] | None = None, plan: str = "chat") -> MagicMock:
    org = MagicMock()
    org.id = 42
    org.plan = plan
    org.enabled_addons = enabled_addons or []
    return org


class TestGetAddons:
    @pytest.mark.asyncio
    async def test_returns_current_state(self) -> None:
        from app.api.admin.settings import get_addons

        perms = make_perms(role="admin", org_id=42, enabled_addons=["scribe"])
        result = await get_addons(perms=perms, db=AsyncMock())
        assert result.enabled_addons == ["scribe"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_by_default(self) -> None:
        from app.api.admin.settings import get_addons

        perms = make_perms(role="admin", org_id=42, enabled_addons=[])
        result = await get_addons(perms=perms, db=AsyncMock())
        assert result.enabled_addons == []


class TestUpdateAddons:
    @pytest.mark.asyncio
    async def test_patch_valid_addon_updates_org(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=[])
        perms = make_perms(role="admin", org_id=42, enabled_addons=[])
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=org)
        body = AddonsUpdate(enabled_addons=["scribe"])
        with (
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event") as mock_emit,
        ):
            result = await update_addons(body=body, perms=perms, db=mock_db)
        assert result.enabled_addons == ["scribe"]
        assert org.enabled_addons == ["scribe"]
        mock_db.commit.assert_called_once()
        assert mock_emit.call_args.args[0] == "tenant.addons_updated"

    @pytest.mark.asyncio
    async def test_patch_unknown_addon_returns_400(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        perms = make_perms(role="admin", org_id=42)
        body = AddonsUpdate(enabled_addons=["hacker_product"])
        with pytest.raises(HTTPException) as exc_info:
            await update_addons(body=body, perms=perms, db=AsyncMock())
        assert exc_info.value.status_code == 400
        assert "hacker_product" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_patch_both_addons(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org()
        perms = make_perms(role="admin", org_id=42)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=org)
        body = AddonsUpdate(enabled_addons=["scribe", "docs"])
        with (
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, perms=perms, db=mock_db)
        assert set(result.enabled_addons) == {"scribe", "docs"}

    @pytest.mark.asyncio
    async def test_patch_empty_disables_all(self) -> None:
        from app.api.admin.settings import AddonsUpdate, update_addons

        org = _mock_org(enabled_addons=["scribe"])
        perms = make_perms(role="admin", org_id=42, enabled_addons=["scribe"])
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=org)
        body = AddonsUpdate(enabled_addons=[])
        with (
            patch("app.api.admin.settings.log_event", new=AsyncMock()),
            patch("app.api.admin.settings.emit_event"),
        ):
            result = await update_addons(body=body, perms=perms, db=mock_db)
        assert result.enabled_addons == []

    # NOTE: `test_non_admin_rejected` was removed in SPEC-PORTAL-RBAC-REFACTOR-001
    # Phase 2a. The role gate is now enforced via
    # `Depends(get_caller_at_least(ProfileRole.ADMIN))` and pinned in
    # `tests/test_permissions.py::test_get_caller_at_least_role_matrix`
    # for every (caller_role, required_role) pair. Keeping a copy here
    # would test the FastAPI dependency-injection wiring, not endpoint
    # behaviour — that lives in the FastAPI/Starlette test layer
    # (`tests/test_app_chat.py` style), not unit tests.
