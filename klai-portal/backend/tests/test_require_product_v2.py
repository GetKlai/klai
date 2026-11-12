"""SPEC-PORTAL-RBAC-001: require_product single-layer behaviour.

There is one ``require_product`` (app.core.permissions). It reads the products
that ``resolve_user_permissions`` derived for the caller's membership in the
session organisation, so these tests run the real resolver over a mocked row and
then the dependency: the product must be in the workspace features AND the
member's role rank must meet FEATURE_MIN_PROFILE, with no admin bypass.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.permissions import refuse_inactive_membership, require_product, resolve_user_permissions


async def _perms(role: str, unlocked: list[str] | None = None, status: str = "active"):
    org = SimpleNamespace(
        id=7,
        zitadel_org_id="zorg",
        slug="example-org",
        plan="chat",
        platform_unlocked_features=unlocked or [],
        provisioning_status="active",
    )
    user = SimpleNamespace(role=role, seat_type="chat", status=status)
    result = MagicMock()
    result.one_or_none.return_value = (org, user)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    perms = await resolve_user_permissions("user-1", db)
    assert perms is not None
    return perms


@pytest.mark.asyncio
async def test_grants_when_feature_in_effective_products() -> None:
    perms = await _perms("personal")
    assert await require_product("chat")(perms=perms) is perms


@pytest.mark.asyncio
async def test_denies_when_feature_missing() -> None:
    perms = await _perms("personal")
    with pytest.raises(HTTPException) as exc_info:
        await require_product("scribe")(perms=perms)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Product not available: scribe"


@pytest.mark.asyncio
async def test_admin_blocked_when_addon_off() -> None:
    """No admin bypass: if scribe is OFF at workspace level, even admin is denied."""
    perms = await _perms("admin")
    with pytest.raises(HTTPException) as exc_info:
        await require_product("scribe")(perms=perms)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_passes_when_addon_on() -> None:
    """Admin's role rank exceeds FEATURE_MIN_PROFILE['scribe'] = 'company'."""
    perms = await _perms("admin", ["scribe"])
    assert await require_product("scribe")(perms=perms) is perms


@pytest.mark.asyncio
async def test_personal_blocked_even_if_addon_on() -> None:
    """Personal rank is below the 'company' floor for scribe."""
    perms = await _perms("personal", ["scribe"])
    with pytest.raises(HTTPException) as exc_info:
        await require_product("scribe")(perms=perms)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_company_passes_when_addon_on() -> None:
    perms = await _perms("company", ["scribe"])
    assert await require_product("scribe")(perms=perms) is perms


@pytest.mark.asyncio
async def test_unknown_user_has_no_permissions() -> None:
    """A user without a portal_users row resolves to no permissions, so get_caller answers 404 before any product gate."""
    no_row = MagicMock()
    no_row.one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=no_row)
    assert await resolve_user_permissions("ghost", db) is None


@pytest.mark.asyncio
async def test_suspended_member_with_the_product_is_refused_with_403() -> None:
    """A membership that is not active has no product access, whatever the role grants."""
    perms = await _perms("admin", ["scribe"], status="suspended")
    with pytest.raises(HTTPException) as exc_info:
        refuse_inactive_membership(perms)
    assert exc_info.value.status_code == 403
