"""SPEC-PORTAL-RBAC-001: get_effective_products is now a single-query
profile-driven derivation. The legacy "self-healing tenant context" pattern
is gone -- the function reads only from the permissive portal_users +
portal_orgs tables, never from the RLS-protected portal_user_products /
portal_group_products tables.

This file's name is preserved for git-history continuity; the contents are
fully rewritten to assert the new contract.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import entitlements


def _row(role: str, plan: str = "chat", enabled_addons: list[str] | None = None) -> MagicMock:
    """Build a mock row that mirrors the SELECT (role, plan, enabled_addons) result."""
    row = MagicMock()
    row.one_or_none.return_value = (role, plan, enabled_addons or [])
    return row


def _empty_row() -> MagicMock:
    row = MagicMock()
    row.one_or_none.return_value = None
    return row


@pytest.mark.asyncio
async def test_returns_plan_products_for_core_personal() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("personal", "chat", []))

    result = await entitlements.get_effective_products("user-1", db)
    assert sorted(result) == ["chat", "knowledge"]


@pytest.mark.asyncio
async def test_personal_does_not_see_enabled_addon() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("personal", "chat", ["scribe"]))

    result = await entitlements.get_effective_products("user-1", db)
    assert "scribe" not in result


@pytest.mark.asyncio
async def test_company_sees_enabled_addon() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("company", "chat", ["scribe"]))

    result = await entitlements.get_effective_products("user-1", db)
    assert "scribe" in result


@pytest.mark.asyncio
async def test_admin_sees_both_addons() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("admin", "chat", ["scribe", "docs"]))

    result = await entitlements.get_effective_products("user-1", db)
    assert set(result) == {"chat", "knowledge", "scribe", "docs"}


@pytest.mark.asyncio
async def test_returns_empty_when_user_has_no_portal_row() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_row())

    result = await entitlements.get_effective_products("ghost", db)
    assert result == []
    # Only ONE query (the lookup). No second UNION query.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_addon_disabled_at_tenant_level_filters_out() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("admin", "chat", []))

    result = await entitlements.get_effective_products("user-1", db)
    assert "scribe" not in result
    assert "docs" not in result


@pytest.mark.asyncio
async def test_unknown_plan_returns_addon_only_when_threshold_met() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_row("admin", "enterprise_xl", ["scribe"]))

    result = await entitlements.get_effective_products("user-1", db)
    # Unknown plan -> empty plan_features. Addons still apply if profile is high enough.
    assert sorted(result) == ["scribe"]


@pytest.mark.asyncio
async def test_namespace_passes_through_simplenamespace_db() -> None:
    """Sanity check that the function accepts SimpleNamespace-style fakes."""

    async def _execute(_stmt: object) -> object:
        return _row("admin", "chat", [])

    db = SimpleNamespace(execute=_execute)
    result = await entitlements.get_effective_products("user-1", db)  # type: ignore[arg-type]
    assert sorted(result) == ["chat", "knowledge"]
