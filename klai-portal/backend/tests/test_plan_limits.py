"""
Tests for SPEC-PORTAL-UNIFY-KB-001 Phase A: plan_limits module.

Covers:
- KBLimits dataclass structure
- PLAN_LIMITS table integrity per D2 spec
- get_plan_limits helper with fallback
- get_effective_limits signature (R-O1 stub)
- plans.py PLAN_PRODUCTS now includes knowledge for all plans
- system_groups.py label rename: "Chat + Focus" -> "Chat"
"""

import pytest

from app.core.plans import PLAN_PRODUCTS, get_plan_products


class TestPlanProductsKnowledgeAdded:
    """AC-1: All active plans include 'knowledge'."""

    def test_core_plan_includes_knowledge(self) -> None:
        assert "knowledge" in get_plan_products("core")

    def test_professional_plan_includes_knowledge(self) -> None:
        assert "knowledge" in get_plan_products("professional")

    def test_complete_plan_includes_knowledge(self) -> None:
        assert "knowledge" in get_plan_products("complete")

    def test_core_plan_products_are_chat_and_knowledge(self) -> None:
        """Core plan: chat + knowledge (no scribe)."""
        products = set(get_plan_products("core"))
        assert products == {"chat", "knowledge"}

    def test_professional_plan_products_include_scribe(self) -> None:
        """Professional plan: chat + scribe + knowledge."""
        products = set(get_plan_products("professional"))
        assert products == {"chat", "scribe", "knowledge"}

    def test_complete_plan_products_include_scribe(self) -> None:
        """Complete plan: chat + scribe + knowledge."""
        products = set(get_plan_products("complete"))
        assert products == {"chat", "scribe", "knowledge"}

    def test_free_plan_still_has_no_products(self) -> None:
        assert get_plan_products("free") == []

    def test_all_non_free_plans_have_knowledge(self) -> None:
        for plan in ("core", "professional", "complete"):
            assert "knowledge" in PLAN_PRODUCTS[plan], f"Plan '{plan}' missing knowledge"


class TestSystemGroupsLabel:
    """AC-7: 'Chat + Focus' label renamed to 'Chat'."""

    def test_chat_group_label_is_chat(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        chat_group = next((g for g in SYSTEM_GROUPS if g["system_key"] == "chat"), None)
        assert chat_group is not None, "Chat system group not found"
        assert chat_group["name"] == "Chat", f"Expected 'Chat', got '{chat_group['name']}'"

    def test_no_system_group_has_focus_in_name(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        for group in SYSTEM_GROUPS:
            assert "Focus" not in group["name"], f"Group '{group['name']}' still contains 'Focus'"

    def test_chat_system_key_unchanged(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        chat_group = next((g for g in SYSTEM_GROUPS if g["system_key"] == "chat"), None)
        assert chat_group is not None
        assert chat_group["system_key"] == "chat"

    def test_chat_group_products_unchanged(self) -> None:
        from app.core.system_groups import SYSTEM_GROUPS

        chat_group = next((g for g in SYSTEM_GROUPS if g["system_key"] == "chat"), None)
        assert chat_group is not None
        assert chat_group["products"] == ["chat"]


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
    """AC-1/AC-2: PLAN_LIMITS table integrity per D2 spec."""

    def test_plan_limits_is_importable(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS  # noqa: F401

    def test_plan_limits_has_core_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "core" in PLAN_LIMITS

    def test_plan_limits_has_professional_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "professional" in PLAN_LIMITS

    def test_plan_limits_has_complete_entry(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert "complete" in PLAN_LIMITS

    def test_every_plan_products_key_has_limits_entry(self) -> None:
        """Every plan in PLAN_PRODUCTS must have a matching PLAN_LIMITS entry."""
        from app.core.plan_limits import PLAN_LIMITS

        for plan in PLAN_PRODUCTS:
            if plan == "free":
                continue  # free plan has no KBLimits entry (optional)
            assert plan in PLAN_LIMITS, f"PLAN_LIMITS missing entry for plan '{plan}'"

    def test_core_limits_max_personal_kbs_is_5(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["core"].max_personal_kbs_per_user == 5

    def test_professional_limits_max_personal_kbs_is_5(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["professional"].max_personal_kbs_per_user == 5

    def test_complete_limits_max_personal_kbs_is_none(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["complete"].max_personal_kbs_per_user is None

    def test_core_limits_max_items_per_kb_is_20(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["core"].max_items_per_kb == 20

    def test_professional_limits_max_items_per_kb_is_20(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["professional"].max_items_per_kb == 20

    def test_complete_limits_max_items_per_kb_is_none(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["complete"].max_items_per_kb is None

    def test_core_cannot_create_org_kbs(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["core"].can_create_org_kbs is False

    def test_professional_cannot_create_org_kbs(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["professional"].can_create_org_kbs is False

    def test_complete_can_create_org_kbs(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["complete"].can_create_org_kbs is True

    def test_core_capabilities_is_empty(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["core"].capabilities == frozenset()

    def test_professional_capabilities_is_empty(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["professional"].capabilities == frozenset()

    def test_complete_capabilities_contains_expected_strings(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS

        expected = {"kb.connectors", "kb.members", "kb.taxonomy", "kb.advanced", "kb.gaps"}
        assert PLAN_LIMITS["complete"].capabilities == frozenset(expected)

    def test_core_and_professional_limits_are_equal(self) -> None:
        """D2: professional is intentionally identical to core (consistency)."""
        from app.core.plan_limits import PLAN_LIMITS

        assert PLAN_LIMITS["core"] == PLAN_LIMITS["professional"]


class TestGetPlanLimits:
    """get_plan_limits helper with fallback."""

    def test_get_plan_limits_returns_core(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("core") == PLAN_LIMITS["core"]

    def test_get_plan_limits_returns_professional(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("professional") == PLAN_LIMITS["professional"]

    def test_get_plan_limits_returns_complete(self) -> None:
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        assert get_plan_limits("complete") == PLAN_LIMITS["complete"]

    def test_get_plan_limits_unknown_plan_falls_back_to_core(self) -> None:
        """Unknown plan falls back to core (safe default = most restricted)."""
        from app.core.plan_limits import PLAN_LIMITS, get_plan_limits

        result = get_plan_limits("nonexistent")
        assert result == PLAN_LIMITS["core"]


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
        mock_org.plan = "core"
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
        assert result == PLAN_LIMITS["core"]
