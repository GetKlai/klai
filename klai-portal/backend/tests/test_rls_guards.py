"""Regression tests for the RLS silent-filter guards.

Covers two defensive layers that prevent the 2026-04-16 Voys provisioning
incident class of bug from re-occurring:

  1. `tenant_scoped_session` / `cross_org_session` bind their scope on
     `session.info` before yielding, so every transaction the session opens
     re-declares it via the `after_begin` listener.

  2. The `rls_guard` after_cursor_execute listener detects rowcount=0 DML
     on known RLS-scoped tables and logs an error (or raises in strict
     mode for tests).

The pin/reset ordering these helpers used to guarantee is gone (2026-08-13):
connections are checked out per transaction, and the tenant GUCs are
transaction-local, so there is nothing to pin and nothing to reset. What is
asserted here now is the *state* the helper leaves on the session plus the
conditional immediate apply.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core import database as db_module
from app.core.rls_guard import (
    RLS_DML_TABLES,
    _extract_dml_table,
    _on_after_cursor_execute,
)


class FakeSession:
    """Stand-in for an AsyncSession handed out by ``AsyncSessionLocal``.

    ``in_transaction`` is parameterised because it is what decides whether the
    helpers issue an immediate ``set_config`` at block entry: a fresh session
    has no open transaction, so `_apply_tenant_context` returns early and the
    context lands at the next BEGIN instead.
    """

    def __init__(self, *, in_transaction: bool = False, calls: list[str] | None = None):
        # `set_tenant` / `cross_org_session` write the scope here before applying it.
        self.info: dict = {}
        self.calls: list[str] = calls if calls is not None else []
        self._in_transaction = in_transaction

    def in_transaction(self) -> bool:
        return self._in_transaction

    def in_nested_transaction(self) -> bool:
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "set_config" in sql and params:
            self.calls.append(f"apply:org={params['org_id']!r},cross={params['cross_org_admin']!r}")
        return SimpleNamespace(rowcount=-1)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# tenant_scoped_session — scope binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_session_binds_scope_before_yield(monkeypatch):
    """The tenant scope must be on `session.info` by the time the body runs.

    That is the whole contract now: `after_begin` reads `session.info` at every
    BEGIN, so a scope present before the first statement is a scope applied to
    every transaction — including the ones after a commit.
    """
    session = FakeSession()
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    async with db_module.tenant_scoped_session(42) as scoped:
        assert scoped is session
        assert session.info["tenant_org_id"] == 42
        session.calls.append("yield")

    # No transaction was open at entry, so no immediate apply fired — and there
    # is no cleanup step at all. Anything else here would be leftover machinery.
    assert session.calls == ["yield"]


@pytest.mark.asyncio
async def test_tenant_scoped_session_applies_immediately_when_transaction_open(monkeypatch):
    """With a transaction already open, `set_tenant` must patch it in place.

    `after_begin` fired for that transaction before the scope existed, so it ran
    with empty values; without the immediate apply the rest of the transaction
    would keep running under no tenant context.
    """
    session = FakeSession(in_transaction=True)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    async with db_module.tenant_scoped_session(42):
        pass

    assert session.calls == ["apply:org='42',cross=''"]


@pytest.mark.asyncio
async def test_tenant_scoped_session_scope_survives_a_raising_body(monkeypatch):
    """A raising body must not trigger any teardown SQL.

    The scope is Python-side state on a session that is about to be closed, and
    the GUCs are transaction-local — the exception path has nothing to undo.
    """
    session = FakeSession()
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    with pytest.raises(RuntimeError, match="boom"):
        async with db_module.tenant_scoped_session(7):
            raise RuntimeError("boom")

    assert session.info["tenant_org_id"] == 7
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_tenant_rejects_savepoint_mutation():
    """Mutating tenant scope inside a SAVEPOINT must fail loud.

    PostgreSQL restores SET LOCAL on ROLLBACK TO SAVEPOINT, so the database
    would revert to the enclosing scope while `session.info` keeps the new one.
    """
    session = FakeSession(in_transaction=True)
    session.in_nested_transaction = lambda: True  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="SAVEPOINT"):
        await db_module.set_tenant(session, 42)


# ---------------------------------------------------------------------------
# cross_org_session — explicit bypass helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_session_sets_bypass_flag(monkeypatch):
    """Records `cross_org_admin` on session.info for the whole block.

    The SQL helper `_rls_current_org_id()` reads the rendered GUC and returns
    NULL (policy IS NULL branch matches everything). No reset on exit: the flag
    is bound transaction-locally, so closing the session ends the bypass.
    """
    session = FakeSession()
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    async with db_module.cross_org_session() as scoped:
        assert scoped is session
        assert session.info["cross_org_admin"] is True
        session.calls.append("yield")

    assert session.calls == ["yield"]


@pytest.mark.asyncio
async def test_cross_org_session_applies_immediately_when_transaction_open(monkeypatch):
    """An already-open transaction must pick the bypass up right away."""
    session = FakeSession(in_transaction=True)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    async with db_module.cross_org_session():
        pass

    assert session.calls == ["apply:org='',cross='true'"]


@pytest.mark.asyncio
async def test_cross_org_session_bypass_does_not_outlive_the_block(monkeypatch):
    """Even when the body raises, the bypass cannot reach another request.

    It never leaves this session: the flag lives on `session.info` (discarded at
    close) and reaches PostgreSQL only as a transaction-local GUC.
    """
    session = FakeSession(in_transaction=True)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: session)

    with pytest.raises(RuntimeError, match="kaboom"):
        async with db_module.cross_org_session():
            raise RuntimeError("kaboom")

    # One apply (the bypass), no teardown statement.
    assert session.calls == ["apply:org='',cross='true'"]


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
