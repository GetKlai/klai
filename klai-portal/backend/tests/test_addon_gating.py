from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_org(enabled_addons=None):
    org = MagicMock()
    org.id = 1
    org.enabled_addons = enabled_addons or []
    return org


def _make_user(role="company"):
    user = MagicMock()
    user.role = role
    return user


def _make_db_one_or_none(user, org):
    db = AsyncMock()
    row_result = MagicMock()
    row_result.one_or_none.return_value = (user, org)
    db.execute.return_value = row_result
    return db


class TestTenantAddOnGating:
    @pytest.mark.asyncio
    async def test_scribe_denied_when_tenant_not_enabled(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user()
        org = _make_org(enabled_addons=[])
        db = _make_db_one_or_none(user, org)
        dep = require_product("scribe")
        with (
            patch("app.api.dependencies.get_current_user_id", return_value="user-1"),
            patch("app.api.dependencies.get_effective_products", return_value=["chat", "scribe"]),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(user_id="user-1", db=db)
        assert exc_info.value.status_code == 403
        assert "not enabled for tenant" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_scribe_denied_when_user_lacks_entitlement(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user()
        org = _make_org(enabled_addons=["scribe"])
        db = _make_db_one_or_none(user, org)
        dep = require_product("scribe")
        with (
            patch("app.api.dependencies.get_current_user_id", return_value="user-1"),
            patch("app.api.dependencies.get_effective_products", return_value=["chat"]),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(user_id="user-1", db=db)
        assert exc_info.value.status_code == 403
        assert "Product access required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_scribe_allowed_when_both_layers_pass(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user()
        org = _make_org(enabled_addons=["scribe"])
        db = _make_db_one_or_none(user, org)
        dep = require_product("scribe")
        with (
            patch("app.api.dependencies.get_current_user_id", return_value="user-1"),
            patch("app.api.dependencies.get_effective_products", return_value=["chat", "scribe"]),
        ):
            await dep(user_id="user-1", db=db)

    @pytest.mark.asyncio
    async def test_admin_blocked_by_tenant_level_disable(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user(role="admin")
        org = _make_org(enabled_addons=[])
        db = _make_db_one_or_none(user, org)
        dep = require_product("scribe")
        with patch("app.api.dependencies.get_current_user_id", return_value="admin-1"):
            with pytest.raises(HTTPException) as exc_info:
                await dep(user_id="admin-1", db=db)
        assert exc_info.value.status_code == 403
        assert "not enabled for tenant" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_admin_passes_when_tenant_enabled(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user(role="admin")
        org = _make_org(enabled_addons=["scribe"])
        db = _make_db_one_or_none(user, org)
        dep = require_product("scribe")
        with patch("app.api.dependencies.get_current_user_id", return_value="admin-1"):
            await dep(user_id="admin-1", db=db)

    @pytest.mark.asyncio
    async def test_non_addon_uses_single_layer(self) -> None:
        from app.api.dependencies import require_product

        db = AsyncMock()
        role_result = MagicMock()
        role_result.scalar_one_or_none.return_value = "company"
        db.execute.return_value = role_result
        dep = require_product("chat")
        with (
            patch("app.api.dependencies.get_current_user_id", return_value="user-1"),
            patch("app.api.dependencies.get_effective_products", return_value=["chat"]),
        ):
            await dep(user_id="user-1", db=db)

    @pytest.mark.asyncio
    async def test_docs_addon_blocked_by_tenant_disable(self) -> None:
        from app.api.dependencies import require_product

        user = _make_user()
        org = _make_org(enabled_addons=[])
        db = _make_db_one_or_none(user, org)
        dep = require_product("docs")
        with (
            patch("app.api.dependencies.get_current_user_id", return_value="user-1"),
            patch("app.api.dependencies.get_effective_products", return_value=["chat", "docs"]),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(user_id="user-1", db=db)
        assert exc_info.value.status_code == 403
