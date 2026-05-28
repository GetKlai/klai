"""
R5 tests -- PATCH /api/admin/settings auto_accept_same_domain toggle
(SPEC-AUTH-009 R5 + C5.1/C5.2).

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2a: settings endpoints take
``perms: UserPermissions`` directly. The PortalOrg row is loaded via
``db.get(PortalOrg, perms.org_id)`` inside the endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_perms


def _make_org(auto_accept: bool = False, primary_domain: str | None = "acme.nl") -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.name = "Acme"
    org.default_language = "nl"
    org.mfa_policy = "optional"
    org.auto_accept_same_domain = auto_accept
    org.primary_domain = primary_domain
    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-15: OrgSettingsOut now also
    # carries telemetry_level. Pin to the privacy-friendly default for
    # tests that don't explicitly exercise this field.
    org.telemetry_level = "shadow"
    return org


class TestAutoAcceptToggleInSettings:
    def test_org_settings_out_includes_auto_accept(self) -> None:
        """R5: OrgSettingsOut schema must include auto_accept_same_domain field."""
        from app.api.admin.settings import OrgSettingsOut

        m = OrgSettingsOut(
            name="Acme",
            default_language="nl",
            mfa_policy="optional",
            auto_accept_same_domain=False,
        )
        assert m.auto_accept_same_domain is False

    def test_org_settings_update_has_auto_accept_field(self) -> None:
        """R5: OrgSettingsUpdate schema must accept auto_accept_same_domain."""
        from app.api.admin.settings import OrgSettingsUpdate

        m = OrgSettingsUpdate(auto_accept_same_domain=True)
        assert m.auto_accept_same_domain is True

    def test_org_settings_update_auto_accept_defaults_none(self) -> None:
        """C5.2: auto_accept_same_domain is optional; omit = no change."""
        from app.api.admin.settings import OrgSettingsUpdate

        m = OrgSettingsUpdate()
        assert m.auto_accept_same_domain is None


class TestPatchSettingsAutoAccept:
    @pytest.mark.asyncio
    async def test_patch_sets_auto_accept_true(self) -> None:
        """C5.1: PATCH /settings with auto_accept_same_domain=True sets it on the org."""
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=False)
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await update_org_settings(
            body=OrgSettingsUpdate(auto_accept_same_domain=True),
            perms=perms,
            db=db,
        )

        assert org.auto_accept_same_domain is True
        assert result.auto_accept_same_domain is True

    @pytest.mark.asyncio
    async def test_patch_sets_auto_accept_false(self) -> None:
        """C5.1: PATCH /settings with auto_accept_same_domain=False sets it on the org."""
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=True)
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await update_org_settings(
            body=OrgSettingsUpdate(auto_accept_same_domain=False),
            perms=perms,
            db=db,
        )

        assert org.auto_accept_same_domain is False
        assert result.auto_accept_same_domain is False

    @pytest.mark.asyncio
    async def test_patch_without_auto_accept_does_not_change_it(self) -> None:
        """C5.2: Omitting auto_accept_same_domain in PATCH leaves existing value unchanged."""
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=True)
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await update_org_settings(
            body=OrgSettingsUpdate(default_language="en"),
            perms=perms,
            db=db,
        )

        assert org.auto_accept_same_domain is True
        assert result.auto_accept_same_domain is True

    @pytest.mark.asyncio
    async def test_get_settings_returns_auto_accept(self) -> None:
        """GET /settings exposes auto_accept_same_domain to admin."""
        from app.api.admin.settings import get_org_settings

        org = _make_org(auto_accept=True)
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_org_settings(perms=perms, db=db)

        assert result.auto_accept_same_domain is True
        assert result.primary_domain == "acme.nl"

    @pytest.mark.asyncio
    async def test_get_settings_hides_unclaimable_primary_domain(self) -> None:
        from app.api.admin.settings import get_org_settings

        org = _make_org(auto_accept=True, primary_domain="gmail.com")
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_org_settings(perms=perms, db=db)

        assert result.primary_domain is None
        assert result.auto_accept_same_domain is False

    @pytest.mark.asyncio
    async def test_patch_does_not_enable_auto_accept_for_unclaimable_primary_domain(self) -> None:
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=False, primary_domain="gmail.com")
        perms = make_perms(role="admin", org_id=1)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await update_org_settings(
            body=OrgSettingsUpdate(auto_accept_same_domain=True),
            perms=perms,
            db=db,
        )

        assert org.auto_accept_same_domain is False
        assert result.auto_accept_same_domain is False
        assert result.primary_domain is None
