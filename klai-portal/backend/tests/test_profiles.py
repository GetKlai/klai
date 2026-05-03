"""
Tests for SPEC-PORTAL-PROFILES-001 Phase 1: Profile Ladder

Covers REQ-1, REQ-3, REQ-7, REQ-11. Pure unit tests.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.portal import PortalUser


def _mock_user(role="company", org_id=1):
    u = MagicMock(spec=PortalUser)
    u.role = role
    u.org_id = org_id
    u.zitadel_user_id = "test-user"
    return u


class TestProfileLadder:
    def test_ladder_exact_values_in_order(self):
        from app.core.profiles import PROFILE_LADDER

        assert PROFILE_LADDER == ["personal", "company", "kb_manager", "group_manager", "admin"]

    def test_ladder_length(self):
        from app.core.profiles import PROFILE_LADDER

        assert len(PROFILE_LADDER) == 5

    def test_personal_is_lowest(self):
        from app.core.profiles import PROFILE_LADDER

        assert PROFILE_LADDER[0] == "personal"

    def test_admin_is_highest(self):
        from app.core.profiles import PROFILE_LADDER

        assert PROFILE_LADDER[-1] == "admin"

    def test_all_rungs_are_unique(self):
        from app.core.profiles import PROFILE_LADDER

        assert len(PROFILE_LADDER) == len(set(PROFILE_LADDER))


class TestProfileCapabilities:
    def test_capabilities_dict_has_all_rungs(self):
        from app.core.profiles import PROFILE_CAPABILITIES, PROFILE_LADDER

        for rung in PROFILE_LADDER:
            assert rung in PROFILE_CAPABILITIES

    def test_personal_has_kb_create_personal(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.create_personal" in PROFILE_CAPABILITIES["personal"]

    def test_personal_lacks_kb_read_org(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.read_org" not in PROFILE_CAPABILITIES["personal"]

    def test_personal_lacks_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" not in PROFILE_CAPABILITIES["personal"]

    def test_personal_has_kb_connectors_url(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.url" in PROFILE_CAPABILITIES["personal"]

    def test_personal_has_kb_connectors_upload(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.upload" in PROFILE_CAPABILITIES["personal"]

    def test_company_has_kb_read_org(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.read_org" in PROFILE_CAPABILITIES["company"]

    def test_company_has_kb_append_via_chat(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.append_via_chat" in PROFILE_CAPABILITIES["company"]

    def test_company_lacks_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" not in PROFILE_CAPABILITIES["company"]

    def test_kb_manager_has_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" in PROFILE_CAPABILITIES["kb_manager"]

    def test_kb_manager_has_kb_create_org(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.create_org" in PROFILE_CAPABILITIES["kb_manager"]

    def test_kb_manager_has_kb_members(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.members" in PROFILE_CAPABILITIES["kb_manager"]

    def test_kb_manager_has_kb_taxonomy(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.taxonomy" in PROFILE_CAPABILITIES["kb_manager"]

    def test_kb_manager_has_kb_gaps(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.gaps" in PROFILE_CAPABILITIES["kb_manager"]

    def test_group_manager_has_groups_manage(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "groups.manage" in PROFILE_CAPABILITIES["group_manager"]

    def test_group_manager_lacks_org_billing(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "org.billing" not in PROFILE_CAPABILITIES["group_manager"]

    def test_admin_has_org_billing(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "org.billing" in PROFILE_CAPABILITIES["admin"]

    def test_admin_has_org_settings(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "org.settings" in PROFILE_CAPABILITIES["admin"]

    def test_admin_has_groups_invite_users(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "groups.invite_users" in PROFILE_CAPABILITIES["admin"]

    def test_capabilities_are_frozensets(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        for _role, caps in PROFILE_CAPABILITIES.items():
            assert isinstance(caps, frozenset)

    def test_each_rung_is_superset_of_lower(self):
        from app.core.profiles import PROFILE_CAPABILITIES, PROFILE_LADDER

        for i in range(1, len(PROFILE_LADDER)):
            lower = PROFILE_LADDER[i - 1]
            higher = PROFILE_LADDER[i]
            assert PROFILE_CAPABILITIES[lower].issubset(PROFILE_CAPABILITIES[higher])


class TestEffectiveRole:
    def test_returns_user_role_string(self):
        from app.core.profiles import effective_role

        user = _mock_user(role="company")
        assert effective_role(user) == "company"

    def test_personal_role_returned_as_is(self):
        from app.core.profiles import effective_role

        user = _mock_user(role="personal")
        assert effective_role(user) == "personal"

    def test_admin_role_returned_as_is(self):
        from app.core.profiles import effective_role

        user = _mock_user(role="admin")
        assert effective_role(user) == "admin"


class TestHasCapability:
    def test_personal_has_kb_create_personal(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="personal")
        assert has_capability(user, "kb.create_personal") is True

    def test_personal_lacks_kb_read_org(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="personal")
        assert has_capability(user, "kb.read_org") is False

    def test_company_has_kb_read_org(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="company")
        assert has_capability(user, "kb.read_org") is True

    def test_company_lacks_kb_connectors_external(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="company")
        assert has_capability(user, "kb.connectors.external") is False

    def test_kb_manager_has_kb_connectors_external(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="kb_manager")
        assert has_capability(user, "kb.connectors.external") is True

    def test_admin_has_all_capabilities(self):
        from app.core.profiles import PROFILE_CAPABILITIES, has_capability

        user = _mock_user(role="admin")
        for cap in PROFILE_CAPABILITIES["admin"]:
            assert has_capability(user, cap) is True


class TestRequireAtLeast:
    def test_company_passes_when_required_personal(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("personal")
        user = _mock_user(role="company")
        dep(caller_user=user)

    def test_personal_passes_when_required_personal(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("personal")
        user = _mock_user(role="personal")
        dep(caller_user=user)

    def test_personal_blocked_when_required_company(self):
        from fastapi import HTTPException

        from app.core.profiles import _require_at_least

        dep = _require_at_least("company")
        user = _mock_user(role="personal")
        with pytest.raises(HTTPException) as exc_info:
            dep(caller_user=user)
        assert exc_info.value.status_code == 403

    def test_company_blocked_when_required_kb_manager(self):
        from fastapi import HTTPException

        from app.core.profiles import _require_at_least

        dep = _require_at_least("kb_manager")
        user = _mock_user(role="company")
        with pytest.raises(HTTPException) as exc_info:
            dep(caller_user=user)
        assert exc_info.value.status_code == 403

    def test_group_manager_passes_when_required_group_manager(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="group_manager")
        dep(caller_user=user)

    def test_admin_passes_all_requirements(self):
        from app.core.profiles import PROFILE_LADDER, _require_at_least

        user = _mock_user(role="admin")
        for rung in PROFILE_LADDER:
            dep = _require_at_least(rung)
            dep(caller_user=user)

    def test_returns_callable(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("company")
        assert callable(dep)


class TestMigrationMapping:
    def test_admin_maps_to_admin(self):
        from app.core.profiles import ROLE_MIGRATION_MAP

        assert ROLE_MIGRATION_MAP["admin"] == "admin"

    def test_group_admin_maps_to_group_manager(self):
        from app.core.profiles import ROLE_MIGRATION_MAP

        assert ROLE_MIGRATION_MAP["group-admin"] == "group_manager"

    def test_member_maps_to_personal(self):
        from app.core.profiles import ROLE_MIGRATION_MAP

        assert ROLE_MIGRATION_MAP["member"] == "personal"

    def test_migration_map_covers_all_old_roles(self):
        from app.core.profiles import ROLE_MIGRATION_MAP

        old_roles = {"admin", "group-admin", "member"}
        assert set(ROLE_MIGRATION_MAP.keys()) == old_roles


class TestOrgKbGate:
    @pytest.mark.asyncio
    async def test_personal_role_excludes_org_slug(self):
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        slugs = await get_accessible_kb_slugs("alice", db, user_role="personal")
        assert "org" not in slugs

    @pytest.mark.asyncio
    async def test_company_role_includes_org_slug(self):
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        slugs = await get_accessible_kb_slugs("alice", db, user_role="company")
        assert "org" in slugs

    @pytest.mark.asyncio
    async def test_personal_role_still_has_personal_slug(self):
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        slugs = await get_accessible_kb_slugs("alice", db, user_role="personal")
        assert "personal-alice" in slugs

    @pytest.mark.asyncio
    async def test_personal_role_skips_default_org_role_kbs(self):
        from app.services.access import get_accessible_kb_slugs

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result
        slugs = await get_accessible_kb_slugs("alice", db, user_role="personal")
        assert "org" not in slugs
        non_personal = [x for x in slugs if not x.startswith("personal-") and not x.startswith("group:")]
        assert non_personal == []


class TestConnectorAllowlist:
    def test_personal_blocked_from_github_connector(self):
        from fastapi import HTTPException

        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="personal")
        with pytest.raises(HTTPException) as exc_info:
            check_connector_allowed(user, "github")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "connector_not_allowed_for_profile"

    def test_company_blocked_from_github_connector(self):
        from fastapi import HTTPException

        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="company")
        with pytest.raises(HTTPException) as exc_info:
            check_connector_allowed(user, "github")
        assert exc_info.value.status_code == 403

    def test_personal_allowed_url_connector(self):
        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="personal")
        check_connector_allowed(user, "url")

    def test_personal_allowed_upload_connector(self):
        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="personal")
        check_connector_allowed(user, "upload")

    def test_kb_manager_allowed_github_connector(self):
        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="kb_manager")
        check_connector_allowed(user, "github")

    def test_admin_allowed_any_connector(self):
        from app.core.profiles import check_connector_allowed

        user = _mock_user(role="admin")
        check_connector_allowed(user, "github")
        check_connector_allowed(user, "notion")
        check_connector_allowed(user, "url")


class TestGroupManagerGate:
    def test_group_manager_role_index_above_company(self):
        from app.core.profiles import PROFILE_LADDER

        gm_idx = PROFILE_LADDER.index("group_manager")
        company_idx = PROFILE_LADDER.index("company")
        assert gm_idx > company_idx

    def test_company_lacks_groups_manage_capability(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="company")
        assert has_capability(user, "groups.manage") is False

    def test_group_manager_has_groups_manage_capability(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="group_manager")
        assert has_capability(user, "groups.manage") is True

    def test_require_group_manager_blocks_company(self):
        from fastapi import HTTPException

        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="company")
        with pytest.raises(HTTPException) as exc_info:
            dep(caller_user=user)
        assert exc_info.value.status_code == 403

    def test_require_group_manager_allows_admin(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="admin")
        dep(caller_user=user)
