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
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import entitlements


@pytest.mark.asyncio
async def test_self_heals_tenant_context_before_querying(monkeypatch):
    """Function must call set_tenant with the user's org_id BEFORE
    running the union query."""
    calls: list[str] = []

    async def _fake_set_tenant(session, org_id: int) -> None:
        calls.append(f"set_tenant:{org_id}")

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)

    # First execute = lookup user's org_id. Second = union(direct, group).
    org_row = MagicMock()
    org_row.scalar_one_or_none = MagicMock(return_value=42)
    products_row = MagicMock()
    products_row.scalars = MagicMock(return_value=MagicMock(all=lambda: ["chat", "scribe"]))

    async def _execute(_stmt):
        calls.append("execute")
        return org_row if len(calls) == 1 else products_row

    db = SimpleNamespace(execute=_execute)

    result = await entitlements.get_effective_products("user-1", db)  # type: ignore[arg-type]

    assert result == ["chat", "scribe"]
    # Invariant: set_tenant must land BETWEEN the org-lookup and the
    # products query.
    assert calls == ["execute", "set_tenant:42", "execute"]


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

    org_row = MagicMock()
    org_row.scalar_one_or_none = MagicMock(return_value=None)

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
async def test_empty_products_when_user_has_no_assignments(monkeypatch):
    """User exists, org exists, no products assigned → []."""

    async def _fake_set_tenant(_session, _org_id: int) -> None:
        pass

    monkeypatch.setattr(entitlements, "set_tenant", _fake_set_tenant)

    org_row = MagicMock()
    org_row.scalar_one_or_none = MagicMock(return_value=7)
    products_row = MagicMock()
    products_row.scalars = MagicMock(return_value=MagicMock(all=lambda: []))

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[org_row, products_row])

    result = await entitlements.get_effective_products("user-2", db)
    assert result == []
