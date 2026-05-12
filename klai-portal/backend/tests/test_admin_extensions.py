"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 3 — /api/admin/extensions endpoint.

Tests the new unified extensions API used by /admin/settings UI:
- GET (own-org) — admin returns full feature list with status.
- GET (cross-org) — platform-admin only; tenant-admin → 403.
- PATCH (cross-org) — platform-admin only; tenant-admin → 403.
- PATCH validates unknown features (400) and persists to platform_unlocked_features.
- Audit event ``platform_features_updated`` emitted via tenant_lifecycle_events.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.api.admin.extensions import (
    UpdateExtensionsRequest,
    list_extensions,
    update_extensions,
)
from tests.conftest import make_org, make_perms


def _db_with_org(org: MagicMock) -> AsyncMock:
    """Mock AsyncSession that returns the given org on `.execute().scalar_one_or_none()`."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


class TestListExtensionsOwnOrg:
    """Tenant-admin sees own-org extensions, read-only."""

    @pytest.mark.asyncio
    async def test_returns_full_known_features_list(self) -> None:
        perms = make_perms(role="admin", org_id=42, platform_unlocked_features=["scribe", "docs"])
        org = make_org(org_id=42, slug="acme", platform_unlocked_features=["scribe", "docs"])
        db = _db_with_org(org)

        result = await list_extensions(org_slug=None, perms=perms, db=db)
        keys = {item.key for item in result.extensions}
        assert keys == {"partner_api", "widgets", "custom_mcps", "scribe", "docs"}
        enabled = {item.key for item in result.extensions if item.enabled}
        assert enabled == {"scribe", "docs"}

    @pytest.mark.asyncio
    async def test_tenant_admin_cannot_query_other_org(self) -> None:
        perms = make_perms(role="admin", org_id=42, is_platform_admin=False)
        db = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await list_extensions(org_slug="some-other-tenant", perms=perms, db=db)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_manageable_by_caller_false_for_tenant_admin(self) -> None:
        perms = make_perms(role="admin", org_id=42, is_platform_admin=False)
        org = make_org(org_id=42, slug="acme")
        db = _db_with_org(org)
        result = await list_extensions(org_slug=None, perms=perms, db=db)
        for item in result.extensions:
            assert item.manageable_by_caller is False


class TestListExtensionsPlatformAdmin:
    """Platform-admin can query any org by slug."""

    @pytest.mark.asyncio
    async def test_platform_admin_can_query_other_org(self) -> None:
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        target_org = make_org(org_id=42, slug="voys", platform_unlocked_features=["partner_api"])
        db = _db_with_org(target_org)
        result = await list_extensions(org_slug="voys", perms=perms, db=db)
        assert result.org_slug == "voys"
        partner = next(i for i in result.extensions if i.key == "partner_api")
        assert partner.enabled is True

    @pytest.mark.asyncio
    async def test_manageable_by_caller_true_for_platform_admin(self) -> None:
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        org = make_org(org_id=1, slug="getklai")
        db = _db_with_org(org)
        result = await list_extensions(org_slug=None, perms=perms, db=db)
        for item in result.extensions:
            assert item.manageable_by_caller is True


class TestUpdateExtensions:
    """PATCH is platform-admin only and persists the unlock set."""

    @pytest.mark.asyncio
    async def test_tenant_admin_blocked_with_403(self) -> None:
        perms = make_perms(role="admin", org_id=42, is_platform_admin=False)
        body = UpdateExtensionsRequest(org_slug="acme", enabled_features=["scribe"])
        with pytest.raises(HTTPException) as exc:
            await update_extensions(body=body, perms=perms, db=AsyncMock())
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_unknown_feature_returns_400(self) -> None:
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        body = UpdateExtensionsRequest(org_slug="voys", enabled_features=["x_legacy_feature"])
        with pytest.raises(HTTPException) as exc:
            await update_extensions(body=body, perms=perms, db=AsyncMock())
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_platform_admin_replaces_feature_set(self) -> None:
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        target_org = make_org(org_id=42, slug="voys", platform_unlocked_features=["scribe"])
        db = _db_with_org(target_org)
        body = UpdateExtensionsRequest(org_slug="voys", enabled_features=["partner_api", "widgets"])

        with patch("app.api.admin.extensions.emit_lifecycle_event", new=AsyncMock()) as mock_audit:
            result = await update_extensions(body=body, perms=perms, db=db)

        assert sorted(target_org.platform_unlocked_features) == ["partner_api", "widgets"]
        db.commit.assert_called_once()
        mock_audit.assert_called_once()
        # Sanity: response reflects the new state.
        enabled = {item.key for item in result.extensions if item.enabled}
        assert enabled == {"partner_api", "widgets"}

    @pytest.mark.asyncio
    async def test_dedupe_and_sort_in_persisted_set(self) -> None:
        """Caller may send duplicates / unsorted; storage is normalised."""
        perms = make_perms(role="admin", org_id=1, is_platform_admin=True)
        target_org = make_org(org_id=42, slug="voys", platform_unlocked_features=[])
        db = _db_with_org(target_org)
        body = UpdateExtensionsRequest(org_slug="voys", enabled_features=["widgets", "scribe", "widgets", "scribe"])

        with patch("app.api.admin.extensions.emit_lifecycle_event", new=AsyncMock()):
            await update_extensions(body=body, perms=perms, db=db)

        assert target_org.platform_unlocked_features == ["scribe", "widgets"]
