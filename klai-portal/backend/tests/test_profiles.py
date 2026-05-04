"""
Tests for SPEC-PORTAL-PROFILES-001 Phase 1: Profile Ladder

Covers REQ-1, REQ-3, REQ-5, REQ-7, REQ-11. Pure unit tests.

Phase 1.5 update (SPEC v0.2.0):
  - PROFILE_CAPABILITIES simplified to endpoint-checked capabilities only.
  - Removed assertions on kb.create_personal, kb.read_org, kb.append_via_chat,
    groups.manage, org.billing, org.settings — those are direct role checks, not
    capability strings.
  - Added tests for new effective_capabilities intersection semantics.
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
    """SPEC v0.2.0: only endpoint-checked capability strings in PROFILE_CAPABILITIES."""

    def test_capabilities_dict_has_all_rungs(self):
        from app.core.profiles import PROFILE_CAPABILITIES, PROFILE_LADDER

        for rung in PROFILE_LADDER:
            assert rung in PROFILE_CAPABILITIES

    # ── personal ──────────────────────────────────────────────────────────────

    def test_personal_has_kb_connectors(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors" in PROFILE_CAPABILITIES["personal"]

    def test_personal_lacks_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" not in PROFILE_CAPABILITIES["personal"]

    def test_personal_lacks_kb_create_org(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.create_org" not in PROFILE_CAPABILITIES["personal"]

    def test_personal_lacks_kb_members(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.members" not in PROFILE_CAPABILITIES["personal"]

    # Removed in v0.2.0: kb.create_personal is a direct role check, not a capability.
    def test_personal_does_not_have_kb_create_personal(self):
        """kb.create_personal was removed from PROFILE_CAPABILITIES in v0.2.0."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.create_personal" not in PROFILE_CAPABILITIES["personal"]

    # Removed in v0.2.0
    def test_personal_does_not_have_kb_read_org(self):
        """kb.read_org is a direct role check, not a capability string."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.read_org" not in PROFILE_CAPABILITIES["personal"]

    # ── company ───────────────────────────────────────────────────────────────

    def test_company_has_kb_connectors(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors" in PROFILE_CAPABILITIES["company"]

    def test_company_lacks_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" not in PROFILE_CAPABILITIES["company"]

    # Removed in v0.2.0
    def test_company_does_not_have_kb_read_org(self):
        """kb.read_org is a direct role check, not a capability string."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.read_org" not in PROFILE_CAPABILITIES["company"]

    # Removed in v0.2.0
    def test_company_does_not_have_kb_append_via_chat(self):
        """kb.append_via_chat is a direct role check, not a capability string."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.append_via_chat" not in PROFILE_CAPABILITIES["company"]

    # ── kb_manager ────────────────────────────────────────────────────────────

    def test_kb_manager_has_kb_connectors(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors" in PROFILE_CAPABILITIES["kb_manager"]

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

    # group_manager should NOT have groups.manage as a capability string.
    def test_group_manager_does_not_have_groups_manage_capability(self):
        """groups.manage is a direct role check, not a capability string (v0.2.0)."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "groups.manage" not in PROFILE_CAPABILITIES["group_manager"]

    # admin should NOT have org.billing as a capability string.
    def test_admin_does_not_have_org_billing_capability(self):
        """org.billing is a direct role check, not a capability string (v0.2.0)."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "org.billing" not in PROFILE_CAPABILITIES["admin"]

    # ── group_manager == admin in capability set ───────────────────────────────

    def test_group_manager_has_kb_connectors(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors" in PROFILE_CAPABILITIES["group_manager"]

    def test_group_manager_has_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" in PROFILE_CAPABILITIES["group_manager"]

    def test_admin_has_kb_connectors_external(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.connectors.external" in PROFILE_CAPABILITIES["admin"]

    def test_admin_has_kb_members(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert "kb.members" in PROFILE_CAPABILITIES["admin"]

    def test_capabilities_are_frozensets(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        for _role, caps in PROFILE_CAPABILITIES.items():
            assert isinstance(caps, frozenset)

    def test_personal_and_company_have_same_capabilities(self):
        """personal and company share _KB_BASIC_CAPS in v0.2.0."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert PROFILE_CAPABILITIES["personal"] == PROFILE_CAPABILITIES["company"]

    def test_kb_manager_group_manager_admin_have_same_capabilities(self):
        """kb_manager, group_manager, and admin all share _KB_FULL_CAPS in v0.2.0."""
        from app.core.profiles import PROFILE_CAPABILITIES

        assert PROFILE_CAPABILITIES["kb_manager"] == PROFILE_CAPABILITIES["group_manager"]
        assert PROFILE_CAPABILITIES["kb_manager"] == PROFILE_CAPABILITIES["admin"]

    def test_kb_manager_is_superset_of_personal(self):
        from app.core.profiles import PROFILE_CAPABILITIES

        assert PROFILE_CAPABILITIES["personal"].issubset(PROFILE_CAPABILITIES["kb_manager"])


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
    def test_personal_has_kb_connectors(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="personal")
        assert has_capability(user, "kb.connectors") is True

    def test_personal_lacks_kb_connectors_external(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="personal")
        assert has_capability(user, "kb.connectors.external") is False

    def test_personal_lacks_kb_members(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="personal")
        assert has_capability(user, "kb.members") is False

    def test_company_has_kb_connectors(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="company")
        assert has_capability(user, "kb.connectors") is True

    def test_company_lacks_kb_connectors_external(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="company")
        assert has_capability(user, "kb.connectors.external") is False

    def test_kb_manager_has_kb_connectors_external(self):
        from app.core.profiles import has_capability

        user = _mock_user(role="kb_manager")
        assert has_capability(user, "kb.connectors.external") is True

    def test_admin_has_all_kb_capabilities(self):
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

    def test_kb_manager_blocked_when_required_group_manager(self):
        """kb_manager does NOT have group management rights (SPEC v0.2.0 REQ-7)."""
        from fastapi import HTTPException

        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="kb_manager")
        with pytest.raises(HTTPException) as exc_info:
            dep(caller_user=user)
        assert exc_info.value.status_code == 403


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
    def test_group_manager_role_index_above_kb_manager(self):
        from app.core.profiles import PROFILE_LADDER

        gm_idx = PROFILE_LADDER.index("group_manager")
        km_idx = PROFILE_LADDER.index("kb_manager")
        assert gm_idx > km_idx

    def test_require_group_manager_blocks_kb_manager(self):
        """kb_manager should NOT manage groups (H1 review concern)."""
        from fastapi import HTTPException

        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="kb_manager")
        with pytest.raises(HTTPException) as exc_info:
            dep(caller_user=user)
        assert exc_info.value.status_code == 403

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

    def test_require_group_manager_allows_group_manager(self):
        from app.core.profiles import _require_at_least

        dep = _require_at_least("group_manager")
        user = _mock_user(role="group_manager")
        dep(caller_user=user)


class TestProfileLimits:
    def test_all_rungs_have_profile_limits(self):
        from app.core.profiles import PROFILE_LADDER, PROFILE_LIMITS

        for role in PROFILE_LADDER:
            assert role in PROFILE_LIMITS

    def test_personal_max_personal_kbs_is_five(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["personal"].max_personal_kbs_per_user == 5

    def test_personal_max_items_per_kb_is_twenty(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["personal"].max_items_per_kb == 20

    def test_personal_cannot_create_org_kbs(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["personal"].can_create_org_kbs is False

    def test_company_max_personal_kbs_is_five(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["company"].max_personal_kbs_per_user == 5

    def test_company_cannot_create_org_kbs(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["company"].can_create_org_kbs is False

    def test_kb_manager_max_personal_kbs_is_unlimited(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["kb_manager"].max_personal_kbs_per_user is None

    def test_kb_manager_max_items_is_unlimited(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["kb_manager"].max_items_per_kb is None

    def test_kb_manager_can_create_org_kbs(self):
        from app.core.profiles import PROFILE_LIMITS

        assert PROFILE_LIMITS["kb_manager"].can_create_org_kbs is True

    def test_group_manager_same_limits_as_kb_manager(self):
        from app.core.profiles import PROFILE_LIMITS

        km = PROFILE_LIMITS["kb_manager"]
        gm = PROFILE_LIMITS["group_manager"]
        assert gm.max_personal_kbs_per_user == km.max_personal_kbs_per_user
        assert gm.max_items_per_kb == km.max_items_per_kb
        assert gm.can_create_org_kbs == km.can_create_org_kbs

    def test_admin_same_limits_as_kb_manager(self):
        from app.core.profiles import PROFILE_LIMITS

        km = PROFILE_LIMITS["kb_manager"]
        ad = PROFILE_LIMITS["admin"]
        assert ad.max_personal_kbs_per_user == km.max_personal_kbs_per_user
        assert ad.max_items_per_kb == km.max_items_per_kb
        assert ad.can_create_org_kbs == km.can_create_org_kbs


class TestMinWithUnlimited:
    def test_both_finite_returns_min(self):
        from app.core.profiles import _min_with_unlimited

        assert _min_with_unlimited(5, 10) == 5

    def test_first_none_returns_second(self):
        from app.core.profiles import _min_with_unlimited

        assert _min_with_unlimited(None, 7) == 7

    def test_second_none_returns_first(self):
        from app.core.profiles import _min_with_unlimited

        assert _min_with_unlimited(3, None) == 3

    def test_both_none_returns_none(self):
        from app.core.profiles import _min_with_unlimited

        assert _min_with_unlimited(None, None) is None

    def test_equal_values_returns_value(self):
        from app.core.profiles import _min_with_unlimited

        assert _min_with_unlimited(5, 5) == 5


class TestEffectiveKBLimits:
    def test_returns_kblimits_instance(self):
        from app.core.plan_limits import KBLimits
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("personal", "core")
        assert isinstance(result, KBLimits)

    def test_personal_core_both_agree(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("personal", "core")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_personal_complete_profile_lowers(self):
        """complete plan + personal role -> 5/20 (profile is the floor)."""
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("personal", "complete")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_company_core_caps_at_five_twenty(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("company", "core")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_kb_manager_core_plan_lowers(self):
        """core plan + kb_manager role -> 5/20 (plan is the ceiling)."""
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("kb_manager", "core")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_kb_manager_professional_plan_lowers(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("kb_manager", "professional")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_kb_manager_complete_unlimited(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("kb_manager", "complete")
        assert result.max_personal_kbs_per_user is None
        assert result.max_items_per_kb is None
        assert result.can_create_org_kbs is True

    def test_group_manager_complete_unlimited(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("group_manager", "complete")
        assert result.max_personal_kbs_per_user is None
        assert result.can_create_org_kbs is True

    def test_admin_complete_unlimited(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("admin", "complete")
        assert result.max_personal_kbs_per_user is None
        assert result.can_create_org_kbs is True

    def test_admin_core_plan_lowers(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("admin", "core")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_unknown_role_falls_back_to_personal(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("nonexistent_role", "complete")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20
        assert result.can_create_org_kbs is False

    def test_unknown_plan_falls_back_to_core_limits(self):
        from app.core.profiles import effective_kb_limits

        result = effective_kb_limits("kb_manager", "unknown_plan")
        assert result.max_personal_kbs_per_user == 5
        assert result.max_items_per_kb == 20

    def test_effective_limits_are_new_objects(self):
        from app.core.profiles import effective_kb_limits

        r1 = effective_kb_limits("personal", "core")
        r2 = effective_kb_limits("personal", "core")
        assert r1 is not r2
