"""
R5 tests -- PATCH /api/admin/settings auto_accept_same_domain toggle
(SPEC-AUTH-009 R5 + C5.1/C5.2).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_org(auto_accept: bool = False) -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.name = "Acme"
    org.default_language = "nl"
    org.mfa_policy = "optional"
    org.auto_accept_same_domain = auto_accept
    org.primary_domain = None
    return org


def _make_admin_user() -> MagicMock:
    u = MagicMock()
    u.role = "admin"
    return u


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
        caller = _make_admin_user()

        with patch("app.api.admin.settings._get_caller_org", AsyncMock(return_value=(None, org, caller))):
            creds = MagicMock()
            db = AsyncMock()
            result = await update_org_settings(
                body=OrgSettingsUpdate(auto_accept_same_domain=True),
                credentials=creds,
                db=db,
            )

        assert org.auto_accept_same_domain is True
        assert result.auto_accept_same_domain is True

    @pytest.mark.asyncio
    async def test_patch_sets_auto_accept_false(self) -> None:
        """C5.1: PATCH /settings with auto_accept_same_domain=False sets it on the org."""
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=True)
        caller = _make_admin_user()

        with patch("app.api.admin.settings._get_caller_org", AsyncMock(return_value=(None, org, caller))):
            creds = MagicMock()
            db = AsyncMock()
            result = await update_org_settings(
                body=OrgSettingsUpdate(auto_accept_same_domain=False),
                credentials=creds,
                db=db,
            )

        assert org.auto_accept_same_domain is False
        assert result.auto_accept_same_domain is False

    @pytest.mark.asyncio
    async def test_patch_without_auto_accept_does_not_change_it(self) -> None:
        """C5.2: Omitting auto_accept_same_domain in PATCH leaves existing value unchanged."""
        from app.api.admin.settings import OrgSettingsUpdate, update_org_settings

        org = _make_org(auto_accept=True)
        caller = _make_admin_user()

        with patch("app.api.admin.settings._get_caller_org", AsyncMock(return_value=(None, org, caller))):
            creds = MagicMock()
            db = AsyncMock()
            result = await update_org_settings(
                body=OrgSettingsUpdate(default_language="en"),
                credentials=creds,
                db=db,
            )

        assert org.auto_accept_same_domain is True
        assert result.auto_accept_same_domain is True

    @pytest.mark.asyncio
    async def test_get_settings_returns_auto_accept(self) -> None:
        """GET /settings exposes auto_accept_same_domain to admin."""
        from app.api.admin.settings import get_org_settings

        org = _make_org(auto_accept=True)
        caller = _make_admin_user()

        with patch("app.api.admin.settings._get_caller_org", AsyncMock(return_value=(None, org, caller))):
            creds = MagicMock()
            db = AsyncMock()
            result = await get_org_settings(credentials=creds, db=db)

        assert result.auto_accept_same_domain is True
