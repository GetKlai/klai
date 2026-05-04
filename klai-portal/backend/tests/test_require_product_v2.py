"""SPEC-PORTAL-RBAC-001: require_product single-layer behaviour.

After RBAC-001 there is no admin bypass and no two-layer (tenant/user) split.
require_product simply checks whether the feature is in
get_effective_products(user_id). The returned list is itself derived
from (role, plan, enabled_addons) so this test covers the full chain.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org_user_row(role: str, plan: str = "core", enabled_addons: list[str] | None = None) -> MagicMock:
    """Build a mock row that get_effective_products consumes (role, plan, enabled_addons)."""
    row = MagicMock()
    row.one_or_none.return_value = (role, plan, enabled_addons or [])
    return row


@pytest.mark.asyncio
async def test_grants_when_feature_in_effective_products() -> None:
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("personal", "core"))
    dep = require_product("chat")
    with patch("app.api.dependencies.get_current_user_id", return_value="user-1"):
        # Should not raise
        await dep(user_id="user-1", db=db)


@pytest.mark.asyncio
async def test_denies_when_feature_missing() -> None:
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("personal", "core", []))
    dep = require_product("scribe")  # personal + scribe disabled at tenant -> denied
    with patch("app.api.dependencies.get_current_user_id", return_value="user-1"):
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="user-1", db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_blocked_when_addon_off() -> None:
    """No admin bypass: if scribe is OFF at tenant level, even admin is denied."""
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("admin", "core", []))
    dep = require_product("scribe")
    with patch("app.api.dependencies.get_current_user_id", return_value="admin-1"):
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="admin-1", db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_passes_when_addon_on() -> None:
    """Admin's role rank exceeds FEATURE_MIN_PROFILE['scribe'] = 'company'."""
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("admin", "core", ["scribe"]))
    dep = require_product("scribe")
    with patch("app.api.dependencies.get_current_user_id", return_value="admin-1"):
        await dep(user_id="admin-1", db=db)  # no raise


@pytest.mark.asyncio
async def test_personal_blocked_even_if_addon_on() -> None:
    """Personal rank is below the 'company' floor for scribe."""
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("personal", "core", ["scribe"]))
    dep = require_product("scribe")
    with patch("app.api.dependencies.get_current_user_id", return_value="user-1"):
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="user-1", db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_company_passes_when_addon_on() -> None:
    from app.api.dependencies import require_product

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_org_user_row("company", "core", ["scribe"]))
    dep = require_product("scribe")
    with patch("app.api.dependencies.get_current_user_id", return_value="user-1"):
        await dep(user_id="user-1", db=db)


@pytest.mark.asyncio
async def test_unknown_user_denied() -> None:
    """User without a portal_users row -> empty effective products -> 403."""
    from app.api.dependencies import require_product

    db = AsyncMock()
    no_row = MagicMock()
    no_row.one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_row)

    dep = require_product("chat")
    with patch("app.api.dependencies.get_current_user_id", return_value="ghost"):
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="ghost", db=db)
    assert exc_info.value.status_code == 403
