"""SPEC-PORTAL-EXTENSIONS-UNIFY-001: deprecated addons endpoints.

GET /api/admin/settings/addons is a read-only facade returning the
subset of platform_unlocked_features that are user-facing products
(scribe/docs), preserving the legacy response shape for transition.

PATCH /api/admin/settings/addons returns 410 Gone — tenants can no
longer self-toggle. Extension toggling is platform-admin-only via
the new /api/admin/extensions endpoint (Phase 3).
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from tests.conftest import make_perms


class TestGetAddonsFacade:
    """GET surfaces the addon-product subset of platform_unlocked_features."""

    @pytest.mark.asyncio
    async def test_returns_scribe_when_unlocked(self) -> None:
        from app.api.admin.settings import get_addons

        perms = make_perms(
            role="admin",
            org_id=42,
            platform_unlocked_features=["scribe"],
        )
        result = await get_addons(perms=perms, db=AsyncMock())
        assert result.enabled_addons == ["scribe"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_unlocks(self) -> None:
        from app.api.admin.settings import get_addons

        perms = make_perms(role="admin", org_id=42, platform_unlocked_features=[])
        result = await get_addons(perms=perms, db=AsyncMock())
        assert result.enabled_addons == []

    @pytest.mark.asyncio
    async def test_filters_out_pure_platform_gates(self) -> None:
        """widgets / custom_mcps / partner_api are not user-facing products,
        so they must NOT appear in the facade response — even though they
        ARE in platform_unlocked_features."""
        from app.api.admin.settings import get_addons

        perms = make_perms(
            role="admin",
            org_id=42,
            platform_unlocked_features=[
                "widgets",
                "custom_mcps",
                "partner_api",
                "scribe",
                "docs",
            ],
        )
        result = await get_addons(perms=perms, db=AsyncMock())
        assert set(result.enabled_addons) == {"scribe", "docs"}
        for non_product in ("widgets", "custom_mcps", "partner_api"):
            assert non_product not in result.enabled_addons


class TestUpdateAddonsGone:
    """PATCH always returns 410 Gone; the deprecation is hard, not soft."""

    @pytest.mark.asyncio
    async def test_patch_returns_410_gone(self) -> None:
        from app.api.admin.settings import update_addons_gone

        with pytest.raises(HTTPException) as exc_info:
            await update_addons_gone()
        assert exc_info.value.status_code == status.HTTP_410_GONE
        assert "platform admins" in str(exc_info.value.detail).lower()
        assert "/api/admin/extensions" in str(exc_info.value.detail)
