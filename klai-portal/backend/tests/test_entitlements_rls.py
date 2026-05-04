"""SPEC-PORTAL-RBAC-001: get_effective_products no longer reads RLS-strict
tables. It only joins portal_users + portal_orgs (both permissive on the
zitadel_user_id lookup), so the historic 2026-04 RLS-context regression
class cannot recur via this code path.

This file is kept as a regression sentinel: if a future SPEC reintroduces
reads on portal_user_products / portal_group_products inside
get_effective_products, the test below will fail.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import entitlements


@pytest.mark.asyncio
async def test_no_set_tenant_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """RBAC-001: derivation must NOT call set_tenant.

    The old self-healing pattern was a workaround for FastAPI dependency
    ordering. After RBAC-001 the lookup hits only permissive tables, so
    set_tenant has no role here.
    """
    set_tenant_mock = AsyncMock()
    monkeypatch.setattr(entitlements, "set_tenant", set_tenant_mock, raising=False)

    row = MagicMock()
    row.one_or_none.return_value = ("admin", "core", ["scribe"])
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)

    result = await entitlements.get_effective_products("user-1", db)
    assert "scribe" in result
    set_tenant_mock.assert_not_called()


@pytest.mark.asyncio
async def test_single_query_no_union() -> None:
    """RBAC-001: derivation issues exactly ONE SELECT, no UNION over RLS tables."""
    row = MagicMock()
    row.one_or_none.return_value = ("admin", "core", [])
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)

    await entitlements.get_effective_products("user-1", db)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_unprovisioned_user_short_circuits() -> None:
    """RBAC-001: the no-portal-row case still short-circuits without secondary queries."""
    row = MagicMock()
    row.one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)

    result = await entitlements.get_effective_products("ghost", db)
    assert result == []
    assert db.execute.await_count == 1
