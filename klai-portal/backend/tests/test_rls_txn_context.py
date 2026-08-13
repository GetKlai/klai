"""Unit tests for the per-transaction RLS context mechanism (2026-08-13).

Incident: ``PATCH .../connectors/{id}`` 500'd with
``InvalidRequestError: Could not refresh instance`` because ``db.refresh()``
after ``db.commit()`` ran on a pooled connection whose session-level
``app.current_org_id`` belonged to a different tenant.

Structural fix: tenant context is a property of the TRANSACTION. The
``after_begin`` listener on ``_SyncPooledTenantSession`` re-declares all four
RLS GUCs transaction-locally (``is_local=true``) from Python-side state at every
BEGIN, so no statement can ever run under a foreign tenant's context and no GUC
can outlive its transaction.

These tests invoke the listener directly (house pattern D from
``test_rls_guards.py``) — no DB required. The real-PostgreSQL proof lives in
``tests/test_rls_txn_context_postgres.py`` (marker: ``postgres``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import database as db_module


class _RecordingConnection:
    """Minimal stand-in for the ``Connection`` handed to ``after_begin``."""

    def __init__(self, dialect_name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, stmt: object, params: dict[str, Any] | None = None) -> object:
        self.calls.append((str(stmt), params or {}))
        return SimpleNamespace(rowcount=-1)


def _session(info: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(info=info if info is not None else {})


def _txn(*, nested: bool = False) -> SimpleNamespace:
    return SimpleNamespace(nested=nested)


@pytest.fixture(autouse=True)
def _clean_actor_contextvars() -> Any:
    """Actor contextvars are process-global; reset them around every test."""
    actor_token = db_module.current_changed_by_user_id.set(None)
    admin_token = db_module.current_is_platform_admin.set(False)
    yield
    db_module.current_changed_by_user_id.reset(actor_token)
    db_module.current_is_platform_admin.reset(admin_token)


# ---------------------------------------------------------------------------
# after_begin listener — the core invariant
# ---------------------------------------------------------------------------


def test_after_begin_applies_all_four_gucs_transaction_locally() -> None:
    """One statement, four GUCs, every one of them ``is_local=true``.

    ``is_local=true`` is what makes pool pollution structurally impossible:
    the value vanishes at COMMIT/ROLLBACK instead of sticking to the pooled
    connection.
    """
    conn = _RecordingConnection()

    db_module._apply_tenant_context_on_begin(_session({"tenant_org_id": 8}), _txn(), conn)  # type: ignore[arg-type]

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    for guc in (
        "app.current_org_id",
        "app.cross_org_admin",
        "klai.changed_by_user_id",
        "app.is_platform_admin",
    ):
        assert guc in sql, f"{guc} missing from the combined statement: {sql}"
    # Four set_config calls, all is_local=true. A `false` here would re-open
    # the 2026-08-13 incident.
    assert sql.count("true)") == 4, sql
    assert "false)" not in sql, sql
    assert set(params) == {"org_id", "cross_org_admin", "changed_by_user_id", "is_platform_admin"}


def test_after_begin_values_come_from_session_info_and_contextvars() -> None:
    """Tenant scope from ``session.info``, actor identity from contextvars.

    Note the two different truthy literals — ``'true'`` for cross_org_admin,
    ``'1'`` for is_platform_admin. Both are baked into deployed RLS policies.
    """
    db_module.current_changed_by_user_id.set("zitadel-user-42")
    db_module.current_is_platform_admin.set(True)
    conn = _RecordingConnection()

    db_module._apply_tenant_context_on_begin(
        _session({"tenant_org_id": 8, "cross_org_admin": True}),  # type: ignore[arg-type]
        _txn(),
        conn,
    )

    _, params = conn.calls[0]
    assert params == {
        "org_id": "8",
        "cross_org_admin": "true",
        "changed_by_user_id": "zitadel-user-42",
        "is_platform_admin": "1",
    }


def test_after_begin_empty_state_still_executes_with_blank_values() -> None:
    """A blank apply is not a no-op — it is the override.

    Rendering all four GUCs as '' on every BEGIN neutralises any legacy
    session-level value a pooled connection might still carry. Skipping the
    statement when state is empty would let that value leak through.
    """
    conn = _RecordingConnection()

    db_module._apply_tenant_context_on_begin(_session(), _txn(), conn)  # type: ignore[arg-type]

    assert len(conn.calls) == 1
    _, params = conn.calls[0]
    assert params == {
        "org_id": "",
        "cross_org_admin": "",
        "changed_by_user_id": "",
        "is_platform_admin": "",
    }


def test_after_begin_skips_nested_transactions() -> None:
    """SAVEPOINTs inherit the enclosing transaction's GUCs — nothing to establish.

    PostgreSQL DOES restore SET LOCAL values on ROLLBACK TO SAVEPOINT, so
    skipping is correct: the enclosing transaction's context is intact either
    way. (The latent caveat — a mutator changing Python-side state inside a
    nested transaction that later rolls back — is documented on the listener;
    no `begin_nested()` call site exists under app/ today.)
    """
    conn = _RecordingConnection()

    db_module._apply_tenant_context_on_begin(
        _session({"tenant_org_id": 8}),  # type: ignore[arg-type]
        _txn(nested=True),
        conn,
    )

    assert conn.calls == []


def test_after_begin_skips_non_postgresql_dialects() -> None:
    """SQLite/in-memory test rigs have no ``set_config()`` — must not break."""
    conn = _RecordingConnection(dialect_name="sqlite")

    db_module._apply_tenant_context_on_begin(_session({"tenant_org_id": 8}), _txn(), conn)  # type: ignore[arg-type]

    assert conn.calls == []


def test_listener_is_registered_on_the_pooled_sync_session_class() -> None:
    """The listener must hang off ``_SyncPooledTenantSession``, not ``Session``.

    Registering globally on ``Session`` would fire for every third-party or
    test-fixture session in the process, which have no tenant contract.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    assert db_module.PooledTenantSession.sync_session_class is db_module._SyncPooledTenantSession
    assert event.contains(db_module._SyncPooledTenantSession, "after_begin", db_module._apply_tenant_context_on_begin)
    assert not event.contains(Session, "after_begin", db_module._apply_tenant_context_on_begin)


# ---------------------------------------------------------------------------
# set_tenant — session-scoped tenant binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_tenant_writes_session_info_and_applies_immediately() -> None:
    """``after_begin`` already fired for the open transaction with the OLD
    state, so ``set_tenant`` must also apply the new context right away."""
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    await db_module.set_tenant(session, 8)

    assert session.info["tenant_org_id"] == 8
    assert db_module.current_org_id.get() == 8
    assert session.execute.await_count == 1
    sql, params = session.execute.await_args.args
    assert "app.current_org_id" in str(sql)
    assert params["org_id"] == "8"


@pytest.mark.asyncio
async def test_set_tenant_scope_is_per_session_not_per_task() -> None:
    """Two sessions in the same task must be able to hold different scopes.

    A request session (org 8) and a ``tenant_scoped_session(22)`` block opened
    inside it coexist; storing the scope in a contextvar instead of
    ``session.info`` would let the inner block silently retarget the outer one.
    """
    outer = MagicMock()
    outer.info = {}
    outer.execute = AsyncMock()
    inner = MagicMock()
    inner.info = {}
    inner.execute = AsyncMock()

    await db_module.set_tenant(outer, 8)
    await db_module.set_tenant(inner, 22)

    assert outer.info["tenant_org_id"] == 8
    assert inner.info["tenant_org_id"] == 22


# ---------------------------------------------------------------------------
# set_request_actor — task-scoped actor binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_request_actor_sets_both_contextvars_and_applies() -> None:
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    await db_module.set_request_actor(session, "zitadel-user-7", True)

    assert db_module.current_changed_by_user_id.get() == "zitadel-user-7"
    assert db_module.current_is_platform_admin.get() is True
    assert session.execute.await_count == 1
    _, params = session.execute.await_args.args
    assert params["changed_by_user_id"] == "zitadel-user-7"
    assert params["is_platform_admin"] == "1"


@pytest.mark.asyncio
async def test_set_request_actor_clears_platform_admin_for_normal_callers() -> None:
    """Non-platform callers must render '' — not the previous request's '1'."""
    db_module.current_is_platform_admin.set(True)
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    await db_module.set_request_actor(session, "zitadel-user-9", False)

    _, params = session.execute.await_args.args
    assert params["is_platform_admin"] == ""


@pytest.mark.asyncio
async def test_set_request_actor_actor_flows_into_nested_sessions() -> None:
    """Actor identity is task-scoped, so a session opened later in the same
    task inherits it.

    This is the pre-existing gap the change closes: seat-history ``changed_by``
    was NULL for admin actions routed through ``tenant_scoped_session``, because
    the actor GUC only ever landed on the request session's connection.
    """
    request_session = MagicMock()
    request_session.info = {}
    request_session.execute = AsyncMock()

    await db_module.set_request_actor(request_session, "admin-1", False)

    # A background/scoped session opened afterwards renders the same actor.
    conn = _RecordingConnection()
    db_module._apply_tenant_context_on_begin(_session({"tenant_org_id": 8}), _txn(), conn)  # type: ignore[arg-type]

    _, params = conn.calls[0]
    assert params["changed_by_user_id"] == "admin-1"


# ---------------------------------------------------------------------------
# cross_org_scope — bypass on an EXISTING session (no second connection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_scope_sets_flag_and_applies_then_clears() -> None:
    """Enter: flag True + apply. Exit: flag False + apply again.

    Unlike `cross_org_session()` this must not open a session of its own —
    webhook handlers already hold one, and a second checkout per request lets a
    burst exhaust the pool.
    """
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    async with db_module.cross_org_scope(session) as scoped:
        assert scoped is session
        assert session.info["cross_org_admin"] is True
        # The bypass reached SQL, transaction-locally.
        _, params = session.execute.await_args.args
        assert params["cross_org_admin"] == "true"
        assert session.execute.await_count == 1

    assert session.info["cross_org_admin"] is False
    assert session.execute.await_count == 2
    _, params = session.execute.await_args.args
    assert params["cross_org_admin"] == ""


@pytest.mark.asyncio
async def test_cross_org_scope_clears_flag_on_exception() -> None:
    """A raising body must not leave the bypass lit on the caller's session.

    The handler keeps using that session afterwards (set_tenant + INSERT), so a
    leaked flag would give the rest of the request cross-tenant visibility.
    """
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    with pytest.raises(RuntimeError, match="kaboom"):
        async with db_module.cross_org_scope(session):
            raise RuntimeError("kaboom")

    assert session.info["cross_org_admin"] is False
    assert session.execute.await_count == 2
    _, params = session.execute.await_args.args
    assert params["cross_org_admin"] == ""


@pytest.mark.asyncio
async def test_cross_org_scope_does_not_open_a_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit pool-usage guard: zero calls to the session factory."""
    opened: list[object] = []
    monkeypatch.setattr(
        db_module,
        "AsyncSessionLocal",
        lambda *a, **kw: opened.append(1),  # type: ignore[misc,return-value]
    )
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock()

    async with db_module.cross_org_scope(session):
        pass

    assert opened == [], "cross_org_scope must reuse the caller's session"
