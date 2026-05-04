"""Regression tests for get_effective_products self-healing tenant context.

Before 2026-04-21 the function silently relied on the caller having called
set_tenant() first. Two production callers violated that:
  - internal.py:622 /internal/knowledge-feature-check
  - dependencies.require_product (FastAPI resolves in parallel with
    _get_caller_org, no guaranteed ordering)

Under strict RLS policies that meant a PostgreSQL insufficient_privilege
exception on every LibreChat login. The function now resolves the user's
org itself (via the permissive portal_users policy) and calls set_tenant
before querying portal_user_products / portal_group_products.

2026-05-03 (SPEC-PORTAL-PROFILES-001 follow-up): function now also unions
PLAN_PRODUCTS for the user's plan into the result so admins/users without
explicit per-user/per-group product entitlement still see plan-included
products (chat, knowledge) in the sidebar and during gating.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import entitlements


def _make_org_row(org_id: int | None, plan: str | None = "core", enabled_addons: list[str] | None = None):
    """Build a mock row mimicking SQLAlchemy's ``result.one_or_none()`` output.

    Returns a MagicMock whose ``one_or_none()`` returns either a
    ``(org_id, plan, enabled_addons)`` tuple (for found users) or ``None``
    (for missing users).
    """
    row = MagicMock()
    if org_id is None:
        row.one_or_none = MagicMock(return_value=None)
    else:
        row.one_or_none = MagicMock(return_value=(org_id, plan, enabled_addons or []))
    return row


def _make_products_row(products: list[str]):
    products_row = MagicMock()
    products_row.scalars = MagicMock(return_value=MagicMock(all=lambda: products))
    return products_row


@pytest.mark.asyncio
async def test_self_heals_tenant_context_before_querying(monkeypatch):
    """Function must call set_tenant with the user's org_id BEFORE
    running the union query."""
    calls: list[str] = []

    async def _fake_set_tenant(session, org_id: int) -> None:
        calls.append(f"set_tenant:{org_id}")

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(entitlements, "get_plan_products", lambda plan: [])

    # First execute = lookup user's (org_id, plan, enabled_addons). Second = union(direct, group).
    # enabled_addons must include "scribe" so the dormancy filter doesn't strip it.
    org_row = _make_org_row(42, "core", enabled_addons=["scribe"])
    products_row = _make_products_row(["scribe"])

    async def _execute(_stmt):
        calls.append("execute")
        return org_row if len([c for c in calls if c == "execute"]) == 1 else products_row

    db = SimpleNamespace(execute=_execute)

    result = await entitlements.get_effective_products("user-1", db)  # type: ignore[arg-type]

    assert "scribe" in result
    # Invariant: set_tenant must land BETWEEN the org-lookup and the
    # products query.
    set_tenant_idx = calls.index("set_tenant:42")
    execute_indexes = [i for i, c in enumerate(calls) if c == "execute"]
    assert execute_indexes[0] < set_tenant_idx < execute_indexes[1]


@pytest.mark.asyncio
async def test_returns_empty_when_user_has_no_portal_row(monkeypatch):
    """Pre-provisioning / deleted user: return [] without blowing up.

    Previously this would still try to query product tables without
    tenant context and crash under strict RLS.
    """
    set_tenant_called = False

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        nonlocal set_tenant_called
        set_tenant_called = True

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)

    org_row = _make_org_row(None)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=org_row)

    result = await entitlements.get_effective_products("unknown-user", db)

    assert result == []
    # Must NOT set tenant context for a nonexistent user — that would
    # spuriously attribute their session to whatever org_id we guessed.
    assert set_tenant_called is False
    # Only the org-lookup should have run.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_empty_products_when_user_has_no_assignments_and_free_plan(monkeypatch):
    """User exists on free plan with no products assigned → [].

    Free plan has no plan-included products; user has no per-user/per-group
    grants. Expected: empty list.
    """

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(entitlements, "get_plan_products", lambda plan: [])

    org_row = _make_org_row(7, "free")
    products_row = _make_products_row([])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-2", db)
    assert result == []


@pytest.mark.asyncio
async def test_plan_products_included_for_paying_plan(monkeypatch):
    """User on `core` plan with no per-user/group grants → plan-included products only.

    This is the regression case for the post-Phase-3 sidebar bug: admins on
    paying plans had `products = []` because plan-included products weren't
    unioned into the effective set.
    """

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(
        entitlements,
        "get_plan_products",
        lambda plan: ["chat", "knowledge"] if plan == "core" else [],
    )

    org_row = _make_org_row(7, "core")
    products_row = _make_products_row([])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-3", db)
    assert sorted(result) == ["chat", "knowledge"]


@pytest.mark.asyncio
async def test_plan_user_and_group_products_unioned(monkeypatch):
    """Plan + per-user + per-group products are all unioned and deduped.

    User on `professional` plan (chat + knowledge), with `scribe` granted
    via group, and an explicit `docs` user-grant → all four in result.
    """

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(
        entitlements,
        "get_plan_products",
        lambda plan: ["chat", "knowledge"] if plan == "professional" else [],
    )

    # Both add-ons must be enabled at tenant level for the dormancy filter to keep them.
    org_row = _make_org_row(7, "professional", enabled_addons=["scribe", "docs"])
    # The union query returns user + group products together (deduplicated by SQL UNION).
    products_row = _make_products_row(["scribe", "docs"])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-4", db)
    assert sorted(result) == ["chat", "docs", "knowledge", "scribe"]


@pytest.mark.asyncio
async def test_dedupe_when_plan_overlaps_with_explicit_grant(monkeypatch):
    """If a user has `chat` granted explicitly AND chat is plan-included,
    it appears once in the result (set union behaviour)."""

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(
        entitlements,
        "get_plan_products",
        lambda plan: ["chat", "knowledge"] if plan == "core" else [],
    )

    org_row = _make_org_row(7, "core")
    products_row = _make_products_row(["chat"])  # explicit duplicate of plan-included

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-5", db)
    assert sorted(result) == ["chat", "knowledge"]  # no duplicate


@pytest.mark.asyncio
async def test_addon_dormant_when_tenant_toggle_off(monkeypatch):
    """SPEC-PORTAL-PROFILES-001 P2.4 dormancy: a user/group entitlement for
    an add-on is filtered out when the tenant toggle is off. The DB row stays
    (preserves admin work) but `/api/me` and `require_product` agree it's
    inactive.
    """

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(
        entitlements,
        "get_plan_products",
        lambda plan: ["chat", "knowledge"] if plan == "core" else [],
    )

    # User has `scribe` granted (per-user or per-group) but tenant toggle is off.
    org_row = _make_org_row(7, "core", enabled_addons=[])
    products_row = _make_products_row(["scribe"])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-6", db)
    assert "scribe" not in result
    assert sorted(result) == ["chat", "knowledge"]


@pytest.mark.asyncio
async def test_addon_active_when_tenant_toggle_on(monkeypatch):
    """Mirror of the dormant case: same entitlement, tenant flag flipped on,
    add-on now appears."""

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(
        entitlements,
        "get_plan_products",
        lambda plan: ["chat", "knowledge"] if plan == "core" else [],
    )

    org_row = _make_org_row(7, "core", enabled_addons=["scribe"])
    products_row = _make_products_row(["scribe"])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-7", db)
    assert sorted(result) == ["chat", "knowledge", "scribe"]


@pytest.mark.asyncio
async def test_partial_dormancy_keeps_active_addon(monkeypatch):
    """Tenant has only one of two add-ons enabled; the disabled one is
    filtered out, the enabled one survives."""

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)
    monkeypatch.setattr(entitlements, "get_plan_products", lambda plan: [])

    org_row = _make_org_row(7, "core", enabled_addons=["docs"])
    products_row = _make_products_row(["scribe", "docs"])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-8", db)
    assert "docs" in result
    assert "scribe" not in result
