"""Tests for plan-mapping helpers and remaining read-only product endpoints.

After SPEC-PORTAL-RBAC-001 v0.2.0 the per-user / per-group assignment surface
is removed (those endpoints return 410 Gone -- see test_products_gone.py for
the 410 contract). This file keeps:

  * PLAN_FEATURES tests (canonical mapping in app.core.features)
  * list_available_products: returns derive_user_products(caller, plan, addons)
  * get_user_effective_products: returns sourced view (plan/addon)
  * change_plan: simple plan update, no product-row cleanup
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.features import PLAN_FEATURES
from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# PLAN_FEATURES mapping (canonical post-SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1)
# ---------------------------------------------------------------------------


class TestPlanProducts:
    def test_free_plan_has_no_products(self) -> None:
        assert PLAN_FEATURES["free"] == frozenset()

    def test_core_plan_has_chat_and_knowledge(self) -> None:
        assert PLAN_FEATURES["core"] == frozenset({"chat", "knowledge"})

    def test_professional_plan_has_chat_and_knowledge(self) -> None:
        assert PLAN_FEATURES["professional"] == frozenset({"chat", "knowledge"})

    def test_complete_plan_has_chat_and_knowledge(self) -> None:
        assert PLAN_FEATURES["complete"] == frozenset({"chat", "knowledge"})

    def test_unknown_plan_falls_back_to_empty(self) -> None:
        # PLAN_FEATURES.get(...) is the public-safe accessor; unknown plans
        # collapse to an empty set the same way the old get_plan_products did.
        assert PLAN_FEATURES.get("nonexistent", frozenset()) == frozenset()

    def test_plan_features_dict_has_four_entries(self) -> None:
        assert len(PLAN_FEATURES) == 4

    def test_plan_hierarchy_is_superset(self) -> None:
        free = PLAN_FEATURES["free"]
        core = PLAN_FEATURES["core"]
        professional = PLAN_FEATURES["professional"]
        complete = PLAN_FEATURES["complete"]
        assert free.issubset(core)
        assert core.issubset(professional)
        assert professional == complete  # both chat + knowledge


# ---------------------------------------------------------------------------
# list_available_products: profile-driven derivation
# ---------------------------------------------------------------------------


class TestListAvailableProducts:
    """Endpoint returns derived products from the caller's UserPermissions.

    Phase 2a: ``list_available_products`` reads `perms.effective_products`
    directly (same value the resolver would derive from role+plan+addons).
    The non-admin 403 branch is now in `Depends(get_caller_at_least(ADMIN))`
    and pinned in `tests/test_permissions.py`.
    """

    @pytest.mark.asyncio
    async def test_returns_plan_features_for_core(self) -> None:
        from app.api.admin.products import list_available_products

        perms = make_perms(role="admin", plan="core", enabled_addons=[])
        result = await list_available_products(perms=perms, db=AsyncMock())
        assert sorted(result.products) == ["chat", "knowledge"]

    @pytest.mark.asyncio
    async def test_returns_plan_plus_enabled_addons_for_admin(self) -> None:
        from app.api.admin.products import list_available_products

        perms = make_perms(role="admin", plan="core", enabled_addons=["scribe", "docs"])
        result = await list_available_products(perms=perms, db=AsyncMock())
        assert set(result.products) == {"chat", "knowledge", "scribe", "docs"}

    @pytest.mark.asyncio
    async def test_free_plan_returns_empty_when_no_addons(self) -> None:
        from app.api.admin.products import list_available_products

        perms = make_perms(role="admin", plan="free", enabled_addons=[])
        result = await list_available_products(perms=perms, db=AsyncMock())
        assert result.products == []


# ---------------------------------------------------------------------------
# 410 endpoints (formerly assign_product, etc.)
# ---------------------------------------------------------------------------


class TestRemovedAssignmentEndpoints:
    @pytest.mark.asyncio
    async def test_assign_product_gone(self) -> None:
        from app.api.admin.products import assign_product_gone

        with pytest.raises(HTTPException) as exc_info:
            await assign_product_gone(zitadel_user_id="user-1")
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_revoke_product_gone(self) -> None:
        from app.api.admin.products import revoke_product_gone

        with pytest.raises(HTTPException) as exc_info:
            await revoke_product_gone(zitadel_user_id="user-1", product="scribe")
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_get_user_products_gone(self) -> None:
        from app.api.admin.products import get_user_products_gone

        with pytest.raises(HTTPException) as exc_info:
            await get_user_products_gone(zitadel_user_id="user-1")
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_product_summary_gone(self) -> None:
        from app.api.admin.products import product_summary_gone

        with pytest.raises(HTTPException) as exc_info:
            await product_summary_gone()
        assert exc_info.value.status_code == 410


# ---------------------------------------------------------------------------
# Plan change: no product-row cleanup
# ---------------------------------------------------------------------------


class TestPlanChange:
    @pytest.mark.asyncio
    async def test_change_plan_writes_only_org_plan(self) -> None:
        from app.api.admin.settings import change_plan

        org = MagicMock()
        org.plan = "core"

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=org)
        body = MagicMock()
        body.plan = "professional"

        await change_plan(body=body, perms=make_perms(role="admin"), db=mock_db)

        assert org.plan == "professional"
        # No product-row cleanup queries -- products derive from (role, plan, enabled_addons).
        mock_db.delete.assert_not_called()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_change_to_unknown_plan_returns_400(self) -> None:
        from app.api.admin.settings import change_plan

        body = MagicMock()
        body.plan = "enterprise_xl"

        with pytest.raises(HTTPException) as exc_info:
            await change_plan(body=body, perms=make_perms(role="admin"), db=AsyncMock())
        assert exc_info.value.status_code == 400
