"""
Tests for plan_limits module.

SPEC-PORTAL-PLAN-RENAME-001: collapsed legacy 4-tier ladder
(free/core/professional/complete) into the live 2-tier marketing model:

    free       -- internal sentinel (no billing)
    chat       -- "Klai Chat"            (€28/mo)
    knowledge  -- "Klai Chat + Knowledge" (€68/mo) — full unlock

Covers:
- KBLimits dataclass structure
- PLAN_LIMITS table integrity for the new 2-tier set
- get_plan_limits helper with fallback to chat (most-restricted paid tier)
- get_effective_limits signature (R-O1 stub)
"""

import pytest

from app.core.features import PLAN_FEATURES


class TestPlanFeaturesCanonicalSet:
    """SPEC-PORTAL-PLAN-RENAME-001 — only chat/knowledge/free exist."""

    def test_plan_features_keys_are_chat_knowledge_free(self) -> None:
        assert set(PLAN_FEATURES.keys()) == {"free", "chat", "knowledge"}

    def test_chat_plan_includes_chat_and_knowledge(self) -> None:
        """Klai Chat plan: chat + knowledge product, with KB-quota limits."""
        assert PLAN_FEATURES["chat"] == frozenset({"chat", "knowledge"})

    def test_knowledge_plan_includes_chat_and_knowledge(self) -> None:
        """+ Knowledge plan: same products, fully unlocked via PLAN_LIMITS."""
        assert PLAN_FEATURES["knowledge"] == frozenset({"chat", "knowledge"})

    def test_free_plan_has_no_products(self) -> None:
        assert PLAN_FEATURES["free"] == frozenset()

    def test_legacy_slugs_are_gone(self) -> None:
        """Regression guard: 'core', 'professional', 'complete' must not return."""
        for legacy in ("core", "professional", "complete"):
            assert legacy not in PLAN_FEATURES


class TestSystemGroupsRemoved:
    """SPEC-PORTAL-RBAC-001 v0.2.0: system groups are removed.

    Profile is the single writer of `portal_users.role`; add-ons are derived
    from `portal_orgs.enabled_addons` + profile rank. There is no longer any
    role_* or addon_* system group in the registry.
    """

    def test_system_groups_registry_is_empty(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        assert SYSTEM_GROUPS == []

    def test_system_group_role_map_is_empty(self) -> None:
        from app.core.system_groups import SYSTEM_GROUP_ROLE_MAP

        assert SYSTEM_GROUP_ROLE_MAP == {}


class TestKBLimitsDataclass:
    """Verify KBLimits dataclass structure and frozen behaviour."""

    def test_kb_limits_is_importable(self) -> None:
        from app.core.plan_limits import KBLimits  # noqa: F401

    def test_kb_limits_is_frozen(self) -> None:
        from app.core.plan_limits import KBLimits

        limits = KBLimits(
            max_personal_kbs_per_user=5,
            max_items_per_kb=20,
            can_create_org_kbs=False,
            capabilities=frozenset(),
        )
        with pytest.raises((AttributeError, TypeError)):
            limits.max_personal_kbs_per_user = 99  # type: ignore[misc]

    def test_kb_limits_fields(self) -> None:
        from app.core.plan_limits import KBLimits

        limits = KBLimits(
            max_personal_kbs_per_user=5,
            max_items_per_kb=20,
            can_create_org_kbs=False,
            capabilities=frozenset({"kb.connectors"}),
        )
        assert limits.max_personal_kbs_per_user == 5
        assert limits.max_items_per_kb == 20
        assert limits.can_create_org_kbs is False
        assert "kb.connectors" in limits.capabilities

    def test_kb_limits_none_means_unlimited(self) -> None:
        from app.core.plan_limits import KBLimits

        limits = KBLimits(
            max_personal_kbs_per_user=None,
            max_items_per_kb=None,
            can_create_org_kbs=True,
            capabilities=frozenset(),
        )
        assert limits.max_personal_kbs_per_user is None
        assert limits.max_items_per_kb is None


class TestPlanLimitsTable:
    """SPEC-PORTAL-PLAN-RENAME-001: 2-tier paid ladder + free sentinel."""

    def test_plan_limits_is_importable(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS  # noqa: F401

    def test_plan_limits_has_chat_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "chat" in PLAN_LIMITS

    def test_plan_limits_has_knowledge_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "knowledge" in PLAN_LIMITS

    def test_plan_limits_has_free_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "free" in PLAN_LIMITS

    def test_plan_limits_keys_match_plan_features(self) -> None:
        """Every plan in PLAN_FEATURES must have a matching PLAN_LIMITS entry."""
        from app.core.plan_limits import PLAN_LIMITS

        assert set(PLAN_LIMITS.keys()) == set(PLAN_FEATURES.keys())

    def test_chat_limits_max_personal_kbs_is_5(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["chat"].max_personal_kbs_per_user == 5

    def test_knowledge_limits_max_personal_kbs_is_unlimited(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["knowledge"].max_personal_kbs_per_user is None

    def test_chat_limits_max_items_per_kb_is_20(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["chat"].max_items_per_kb == 20

    def test_knowledge_limits_max_items_per_kb_is_unlimited(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["knowledge"].max_items_per_kb is None

    def test_chat_cannot_create_org_kbs(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["chat"].can_create_org_kbs is False

    def test_knowledge_can_create_org_kbs(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["knowledge"].can_create_org_kbs is True

    def test_chat_capabilities_only_kb_connectors(self) -> None:
        """Klai Chat tier: basic personal-KB connectors only."""
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["chat"].capabilities == frozenset({"kb.connectors"})

    def test_knowledge_capabilities_full_unlock(self) -> None:
        """+ Knowledge tier: external connectors, org-KBs, members/taxonomy/gaps."""
        from app.core.plan_limits import PLAN_LIMITS

        expected = {
            "kb.connectors",
            "kb.connectors.external",
            "kb.create_org",
            "kb.members",
            "kb.taxonomy",
            "kb.gaps",
        }
        assert PLAN_LIMITS["knowledge"].capabilities == frozenset(expected)

    def test_free_capabilities_is_empty(self) -> None:
        """Free sentinel grants no capabilities at all."""
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["free"].capabilities == frozenset()


class TestGetPlanLimits:
    """get_plan_limits helper with fallback to chat (cheapest paid)."""

    def test_get_plan_limits_returns_chat(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("chat") == PLAN_LIMITS["chat"]

    def test_get_plan_limits_returns_knowledge(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("knowledge") == PLAN_LIMITS["knowledge"]

    def test_get_plan_limits_returns_free(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("free") == PLAN_LIMITS["free"]

    def test_get_plan_limits_unknown_plan_falls_back_to_chat(self) -> None:
        """Unknown plan falls back to chat (the most-restrictive paid tier).

        Note: free is the sentinel for no-billing; falling back to it would
        silently grant 'no products at all' which is more surprising than
        the cheapest paid tier.
        """
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        result = get_plan_limits("nonexistent")
        assert result == PLAN_LIMITS["chat"]


class TestGetEffectiveLimits:
    """R-O1 stub: get_effective_limits(org_id) signature exists and delegates to get_plan_limits."""

    @pytest.mark.asyncio
    async def test_get_effective_limits_exists(self) -> None:
        from app.core.plan_limits import get_effective_limits  # noqa: F401

    @pytest.mark.asyncio
    async def test_get_effective_limits_returns_kb_limits(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.core.plan_limits import KBLimits, get_effective_limits

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_org = MagicMock()
        mock_org.plan = "chat"
        mock_result.scalar_one_or_none.return_value = mock_org
        mock_db.execute.return_value = mock_result

        result = await get_effective_limits(org_id=1, db=mock_db)
        assert isinstance(result, KBLimits)
        assert result.max_personal_kbs_per_user == 5

    @pytest.mark.asyncio
    async def test_get_effective_limits_unknown_org_falls_back(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.core.plan_limits import PLAN_LIMITS, get_effective_limits

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await get_effective_limits(org_id=99999, db=mock_db)
        assert result == PLAN_LIMITS["chat"]
