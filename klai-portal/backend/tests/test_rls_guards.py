"""Regression tests for the RLS silent-filter guards.

Covers three defensive layers that prevent the 2026-04-16 Voys provisioning
incident class of bug from re-occurring:

  1. `tenant_scoped_session` pins the connection and calls set_tenant in the
     right order, so set_config('app.current_org_id', ...) is visible to
     later statements on the same session.

  2. `pin_session` does the same for an externally-provided session
     (provisioning orchestrator path).

  3. The `rls_guard` after_cursor_execute listener detects rowcount=0 DML
     on known RLS-scoped tables and logs an error (or raises in strict
     mode for tests).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import database as db_module
from app.core.rls_guard import (
    RLS_DML_TABLES,
    _extract_dml_table,
    _on_after_cursor_execute,
)

# ---------------------------------------------------------------------------
# tenant_scoped_session — ordering guarantees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_session_pins_before_set_tenant(monkeypatch):
    """The helper must call session.connection() BEFORE set_config.

    Ordering matters: if set_config fires before pinning, it lands on a
    different pooled connection and RLS silently filters subsequent rows.
    """
    calls: list[str] = []

    class FakeSession:
        async def connection(self):
            calls.append("pin")

        async def rollback(self):
            calls.append("rollback")

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if "set_config" in sql:
                if params and params.get("org_id") == "42":
                    calls.append("set_tenant:42")
                elif "current_org_id" in sql:
                    calls.append("reset_current_org_id")
                elif "cross_org_admin" in sql:
                    calls.append("reset_cross_org_admin")
            return SimpleNamespace(rowcount=-1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSession())

    async with db_module.tenant_scoped_session(42) as session:
        assert session is not None
        calls.append("yield")

    # Critical invariants:
    #   1. pin happens before set_tenant (set_config must see pinned connection)
    #   2. on exit, rollback runs BEFORE set_config resets — otherwise an aborted
    #      transaction from a 42501 RLS fail-loud would trap the reset and leak
    #      app.current_org_id to the next pooled request (2026-04-23 incident).
    #   3. both GUCs are cleared on exit.
    assert calls == [
        "pin",
        "set_tenant:42",
        "yield",
        "rollback",
        "reset_current_org_id",
        "reset_cross_org_admin",
    ]


@pytest.mark.asyncio
async def test_tenant_scoped_session_resets_on_exception(monkeypatch):
    """Tenant context must be reset even if the body raises."""
    calls: list[str] = []

    class FakeSession:
        async def connection(self):
            calls.append("pin")

        async def rollback(self):
            calls.append("rollback")

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if "set_config" in sql:
                if params and params.get("org_id") == "7":
                    calls.append("set")
                elif "current_org_id" in sql:
                    calls.append("reset_current_org_id")
                elif "cross_org_admin" in sql:
                    calls.append("reset_cross_org_admin")
            return SimpleNamespace(rowcount=-1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(RuntimeError, match="boom"):
        async with db_module.tenant_scoped_session(7):
            raise RuntimeError("boom")

    # Even on exception: rollback runs, then both GUCs are cleared.
    assert calls == ["pin", "set", "rollback", "reset_current_org_id", "reset_cross_org_admin"]


# ---------------------------------------------------------------------------
# pin_session — idempotent pin helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_session_calls_connection():
    session = MagicMock()
    session.connection = AsyncMock()
    await db_module.pin_session(session)
    session.connection.assert_awaited_once()


# ---------------------------------------------------------------------------
# cross_org_session — explicit bypass helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_session_sets_and_resets_bypass_flag(monkeypatch):
    """Sets app.cross_org_admin=true on entry and clears it on exit.

    The SQL helper _rls_current_org_id() reads this flag and returns NULL
    (policy IS NULL branch matches everything). Reset on exit is critical —
    otherwise a pooled connection that next serves a tenant request would
    carry the bypass into user code.
    """
    calls: list[str] = []

    class FakeSession:
        async def connection(self):
            calls.append("pin")

        async def rollback(self):
            calls.append("rollback")

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if "set_config" in sql and "cross_org_admin" in sql:
                calls.append("bypass_on" if "'true'" in sql else "bypass_off")
            elif "set_config" in sql and "current_org_id" in sql:
                calls.append("tenant_reset")
            return SimpleNamespace(rowcount=-1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSession())

    async with db_module.cross_org_session() as session:
        assert session is not None
        calls.append("yield")

    # After the 2026-04-23 pool-leak fix, cleanup delegates to
    # _reset_tenant_context which rolls back first, then clears both GUCs
    # (current_org_id before cross_org_admin). An aborted transaction from
    # a 42501 inside the cross-org body would otherwise leak the bypass
    # flag to the next pooled request.
    assert calls == ["pin", "bypass_on", "yield", "rollback", "tenant_reset", "bypass_off"]


@pytest.mark.asyncio
async def test_cross_org_session_clears_bypass_on_exception(monkeypatch):
    """Bypass flag MUST be cleared even if the body raises."""
    calls: list[str] = []

    class FakeSession:
        async def connection(self):
            calls.append("pin")

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if "cross_org_admin" in sql and "'true'" in sql:
                calls.append("bypass_on")
            elif "cross_org_admin" in sql:
                calls.append("bypass_off")
            return SimpleNamespace(rowcount=-1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSession())

    with pytest.raises(RuntimeError, match="kaboom"):
        async with db_module.cross_org_session():
            raise RuntimeError("kaboom")

    assert "bypass_on" in calls and "bypass_off" in calls
    assert calls.index("bypass_on") < calls.index("bypass_off")


# ---------------------------------------------------------------------------
# rls_guard._extract_dml_table — statement parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("UPDATE portal_knowledge_bases SET name='x' WHERE id = 1", ("UPDATE", "portal_knowledge_bases")),
        ("  update portal_groups SET name='y' WHERE id = 2", ("UPDATE", "portal_groups")),
        ("UPDATE \"portal_knowledge_bases\" SET name='x'", ("UPDATE", "portal_knowledge_bases")),
        ("DELETE FROM partner_api_keys WHERE id = 3", ("DELETE", "partner_api_keys")),
        ("DELETE FROM public.portal_groups WHERE id = 4", ("DELETE", "portal_groups")),
        ("UPDATE portal_users SET email='x' WHERE id = 5", None),  # not in RLS_DML_TABLES
        ("SELECT * FROM portal_groups", None),  # not DML
        ("INSERT INTO portal_groups (org_id, name) VALUES (1, 'x')", None),  # INSERT not covered
        ("", None),
    ],
)
def test_extract_dml_table(sql: str, expected: tuple[str, str] | None):
    assert _extract_dml_table(sql) == expected


# ---------------------------------------------------------------------------
# rls_guard._on_after_cursor_execute — the actual guard
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def test_rls_guard_logs_error_on_zero_rowcount_dml(caplog):
    cursor = _FakeCursor(rowcount=0)
    statement = "UPDATE portal_knowledge_bases SET name='x' WHERE id = 99"
    with caplog.at_level(logging.ERROR, logger="app.core.rls_guard"):
        _on_after_cursor_execute(None, cursor, statement, {}, None, False)
    messages = [r.getMessage() for r in caplog.records]
    assert any("RLS silent-filter: UPDATE on portal_knowledge_bases matched 0 rows" in m for m in messages), messages


def test_rls_guard_stays_quiet_on_positive_rowcount(caplog):
    cursor = _FakeCursor(rowcount=1)
    statement = "UPDATE portal_knowledge_bases SET name='x' WHERE id = 1"
    with caplog.at_level(logging.ERROR, logger="app.core.rls_guard"):
        _on_after_cursor_execute(None, cursor, statement, {}, None, False)
    assert caplog.records == []


def test_rls_guard_stays_quiet_on_non_rls_table(caplog):
    cursor = _FakeCursor(rowcount=0)
    statement = "UPDATE portal_users SET email='x' WHERE id = 1"
    with caplog.at_level(logging.ERROR, logger="app.core.rls_guard"):
        _on_after_cursor_execute(None, cursor, statement, {}, None, False)
    assert caplog.records == []


def test_rls_guard_stays_quiet_on_select(caplog):
    cursor = _FakeCursor(rowcount=0)
    statement = "SELECT * FROM portal_knowledge_bases WHERE org_id = 99"
    with caplog.at_level(logging.ERROR, logger="app.core.rls_guard"):
        _on_after_cursor_execute(None, cursor, statement, {}, None, False)
    assert caplog.records == []


def test_rls_guard_strict_mode_raises(monkeypatch):
    monkeypatch.setenv("PORTAL_RLS_GUARD_STRICT", "1")
    cursor = _FakeCursor(rowcount=0)
    statement = "DELETE FROM portal_groups WHERE id = 1"
    with pytest.raises(RuntimeError, match="RLS silent-filter"):
        _on_after_cursor_execute(None, cursor, statement, {}, None, False)


# ---------------------------------------------------------------------------
# RLS_DML_TABLES — canonical list must match pg_policies
# ---------------------------------------------------------------------------


def test_rls_dml_tables_includes_core_tenant_tables():
    # Regression fence: these tables MUST be covered by the guard. Adding
    # a new RLS table? Append it to both pg_policies and RLS_DML_TABLES.
    required = {
        "portal_knowledge_bases",
        "portal_groups",
        "portal_group_products",
        "portal_retrieval_gaps",
        "partner_api_keys",
        "vexa_meetings",
    }
    assert required.issubset(RLS_DML_TABLES)
