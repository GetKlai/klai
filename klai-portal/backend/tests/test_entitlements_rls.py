"""Regression tests for RLS tenant-context handling in get_effective_products.

The function queries portal_user_products and portal_group_products — both
RLS-protected with strict `org_id = app.current_org_id` policies. In April
2026 every login broke because /api/me called get_effective_products on a
session that had no app.current_org_id set; PostgreSQL raised
InsufficientPrivilegeError and the callback page rendered "Login failed
HTTP 500". The fix is for get_effective_products itself to resolve the
user's org and call set_tenant before the UNION query, so individual
call sites (e.g. /api/me, require_product, /internal consumers) do not
each have to remember to do it.

These tests mock the DB session and assert the set_tenant side-effect
with a real PostgreSQL-visible semantics: is set_config called with the
expected org_id BEFORE the UNION query. They do not (and cannot) exercise
the actual RLS policy — SQLite, which backs the pytest suite, has no RLS
— but they lock the invariant that a future refactor would otherwise
silently break in production only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.entitlements import get_effective_products


@pytest.mark.asyncio
async def test_sets_tenant_context_before_union_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """set_tenant must run between the org lookup and the entitlements query."""
    calls: list[tuple[str, object]] = []

    org_scalar = MagicMock()
    org_scalar.scalar_one_or_none.return_value = 42
    products_scalars = MagicMock()
    products_scalars.all.return_value = ["chat", "scribe"]
    products_result = MagicMock()
    products_result.scalars.return_value = products_scalars

    execute_responses: list[object] = [org_scalar, products_result]

    async def tracked_execute(*_args: object, **_kwargs: object) -> object:
        calls.append(("execute", None))
        return execute_responses.pop(0)

    async def mock_set_tenant(_session: object, org_id: int) -> None:
        calls.append(("set_tenant", org_id))

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=tracked_execute)
    monkeypatch.setattr("app.services.entitlements.set_tenant", mock_set_tenant)

    result = await get_effective_products("user-42", db)

    assert result == ["chat", "scribe"]
    assert calls == [
        ("execute", None),  # 1. org_id lookup (portal_users is permissive → safe)
        ("set_tenant", 42),  # 2. RLS context set for the UNION query
        ("execute", None),  # 3. UNION over RLS-protected product tables
    ], f"Call order broke RLS invariant: {calls}"


@pytest.mark.asyncio
async def test_returns_empty_for_unprovisioned_user_without_setting_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Users without a portal_users row short-circuit without touching RLS."""
    org_scalar = MagicMock()
    org_scalar.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=org_scalar)

    set_tenant_mock = AsyncMock()
    monkeypatch.setattr("app.services.entitlements.set_tenant", set_tenant_mock)

    result = await get_effective_products("ghost-user", db)

    assert result == []
    set_tenant_mock.assert_not_called()
    # Only the org_id lookup should run; the UNION query must NOT fire on a
    # session without tenant context, or RLS would reject it.
    assert db.execute.call_count == 1
