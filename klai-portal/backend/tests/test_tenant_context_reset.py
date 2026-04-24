"""Regression tests for the tenant-context reset pool-leak fix (2026-04-23).

Symptom that led to this test:
  - Apr 22: `post_deploy_rls_raise_on_missing_context.sql` flipped portal_users-
    adjacent RLS policies to fail-loud (raise 42501 instead of silent filter).
  - Apr 23: admin login landed on `/no-account` and `/app/chat` inception.

Root cause:
  Any request that ran `set_tenant(org_X)` and then hit a 42501 RLS error left
  the SQLAlchemy transaction in aborted state. The old
  `_reset_tenant_context` ran `SELECT set_config(...)` on the aborted session —
  PostgreSQL rejects every command on an aborted transaction — so the reset
  silently failed via `suppress(Exception)`. The connection returned to the
  pool with `app.current_org_id='X'` still set. The next request (e.g. the
  admin's BFF callback or `/api/me`) picked up that connection, queried
  `portal_users`, and RLS filtered out the admin's row because their org did
  not match `X`. Result: `org_found=false`, `workspace_url=null`, wrong
  redirect.

The fix: `_reset_tenant_context` MUST roll back first so subsequent
`set_config` statements can run on a clean session. It also now clears BOTH
GUCs (`app.current_org_id` and `app.cross_org_admin`) so `cross_org_session`
cannot leak its bypass flag via the same aborted-transaction path.

These tests lock the ordering and the GUC coverage. They do not exercise
PostgreSQL itself — that lives in `scripts/rls-smoke-test.sql` / the
`rls-policy-smoke-test` CI job.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import database as db_module


def _fake_session() -> AsyncMock:
    session = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# _reset_tenant_context — ordering and GUC coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_rolls_back_before_set_config() -> None:
    """rollback() MUST fire before the first set_config.

    If set_config runs first on an aborted session it raises
    ``InFailedSqlTransactionError`` and the suppress swallows it — the pool
    leaks the leftover tenant GUC to the next request.
    """
    calls: list[str] = []
    session = _fake_session()

    async def tracked_rollback() -> None:
        calls.append("rollback")

    async def tracked_execute(stmt: object, *args: object, **kwargs: object) -> object:
        # Record the set_config target rather than the whole text clause.
        text_str = str(stmt)
        if "app.current_org_id" in text_str:
            calls.append("reset_current_org_id")
        elif "app.cross_org_admin" in text_str:
            calls.append("reset_cross_org_admin")
        else:
            calls.append(f"unexpected:{text_str}")
        return MagicMock()

    session.rollback = AsyncMock(side_effect=tracked_rollback)
    session.execute = AsyncMock(side_effect=tracked_execute)

    await db_module._reset_tenant_context(session)

    assert calls[0] == "rollback", f"rollback must run first, got {calls}"
    assert "reset_current_org_id" in calls, calls
    assert "reset_cross_org_admin" in calls, calls


@pytest.mark.asyncio
async def test_reset_clears_both_rls_gucs() -> None:
    """Both current_org_id AND cross_org_admin must be cleared.

    Without this `cross_org_session` could leak the bypass flag to the next
    request — same class of bug but worse because the bypass makes the
    connection see ALL tenants' rows.
    """
    session = _fake_session()

    await db_module._reset_tenant_context(session)

    # rollback + 2 set_config calls
    assert session.rollback.await_count == 1
    assert session.execute.await_count == 2

    rendered = [str(c.args[0]) for c in session.execute.await_args_list]
    assert any("app.current_org_id" in s for s in rendered), rendered
    assert any("app.cross_org_admin" in s for s in rendered), rendered


@pytest.mark.asyncio
async def test_reset_swallows_rollback_failure_and_still_tries_to_clear() -> None:
    """A raised rollback (closed session) must not skip the set_config attempts.

    Defense-in-depth: even if rollback itself fails we still try to clear
    the GUCs. If those also fail the suppress lets the connection return to
    the pool — the next request will either hit a live GUC (and fail safely)
    or a recycled connection from pool_pre_ping.
    """
    session = _fake_session()
    session.rollback = AsyncMock(side_effect=RuntimeError("session already closed"))

    # Should not raise.
    await db_module._reset_tenant_context(session)

    # Still attempted both GUC resets.
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_reset_swallows_set_config_failure_on_first_guc() -> None:
    """A failure clearing current_org_id must not skip clearing cross_org_admin.

    Each set_config is wrapped in its own suppress block for exactly this.
    """
    session = _fake_session()

    call_counter = {"n": 0}

    async def execute_side_effect(*_args: object, **_kwargs: object) -> object:
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            raise RuntimeError("set_config failed on current_org_id")
        return MagicMock()

    session.execute = AsyncMock(side_effect=execute_side_effect)

    await db_module._reset_tenant_context(session)

    # Both set_config calls attempted despite the first one raising.
    assert session.execute.await_count == 2


# ---------------------------------------------------------------------------
# cross_org_session — no more double-reset of app.cross_org_admin
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _pin_and_reset_connection — checkout-time defense against pool pollution
# ---------------------------------------------------------------------------
#
# Symptom that led to these tests (2026-04-24):
#   Apr 23 — getklai.getklai.com chat: `/api/app/templates` and
#   `/api/app/chat-health` returned intermittent 404 "Organisation not found"
#   for a valid session, while `/api/app/knowledge-bases` with the same cookie
#   in the same second returned 200. Caddy logs showed alternating statuses
#   tied to which pooled DB connection served the request.
#
# Root cause:
#   `_get_caller_org` queries portal_users (RLS: `org_id = GUC OR GUC IS NULL`)
#   BEFORE calling set_tenant. If the checked-out connection had a stale
#   `app.current_org_id` from a prior request whose cleanup `_reset_tenant_context`
#   suppressed an error (aborted transaction, closed connection), the user row
#   was filtered out and the handler raised 404.
#
#   Cleanup-time reset was not enough — any silent failure there leaks the GUC
#   to the next checkout. These tests lock the new checkout-time reset.


@pytest.mark.asyncio
async def test_pin_and_reset_clears_stale_tenant_at_checkout() -> None:
    """`_pin_and_reset_connection` MUST clear any leftover tenant GUC.

    Without this, a pooled connection whose prior cleanup suppressed an
    error returns to the next request with the stale `app.current_org_id`,
    and RLS silently filters portal_users for the wrong tenant.
    """
    session = _fake_session()
    session.connection = AsyncMock()

    await db_module._pin_and_reset_connection(session)

    # Connection pinned exactly once before reset runs.
    assert session.connection.await_count == 1
    # Reset: rollback + two set_config calls (current_org_id + cross_org_admin).
    assert session.rollback.await_count == 1
    assert session.execute.await_count == 2
    rendered = [str(c.args[0]) for c in session.execute.await_args_list]
    assert any("app.current_org_id" in s for s in rendered), rendered
    assert any("app.cross_org_admin" in s for s in rendered), rendered


@pytest.mark.asyncio
async def test_pin_runs_connection_before_reset() -> None:
    """Connection MUST be pinned before the set_config statements run.

    If the reset landed on a non-pinned session, SQLAlchemy could route each
    set_config to a different pooled connection and the RLS GUC would end up
    on the wrong physical connection — re-introducing the same class of bug.
    """
    session = AsyncMock()
    calls: list[str] = []

    async def tracked_connection() -> None:
        calls.append("connection")

    async def tracked_rollback() -> None:
        calls.append("rollback")

    async def tracked_execute(*_args: object, **_kwargs: object) -> object:
        calls.append("execute")
        return MagicMock()

    session.connection = AsyncMock(side_effect=tracked_connection)
    session.rollback = AsyncMock(side_effect=tracked_rollback)
    session.execute = AsyncMock(side_effect=tracked_execute)

    await db_module._pin_and_reset_connection(session)

    assert calls[0] == "connection", f"connection() must run first, got {calls}"
    # Then the reset sequence (rollback, execute, execute).
    assert calls[1:] == ["rollback", "execute", "execute"], calls


@pytest.mark.asyncio
async def test_get_db_clears_stale_guc_before_yielding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the getklai.getklai.com intermittent-404 incident.

    Before the fix, `get_db` pinned the connection but did NOT reset the
    tenant GUC at checkout. If the previous request leaked `app.current_org_id`
    into the pool, the very next `_get_caller_org` query ran with the wrong
    tenant and returned 404 for a valid session.

    After the fix, `_pin_and_reset_connection` fires before yielding, so the
    handler sees a clean GUC and its own `set_tenant` lands reliably.
    """
    fake_session = _fake_session()
    fake_session.connection = AsyncMock()

    class FakeSessionCM:
        async def __aenter__(self) -> AsyncMock:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSessionCM())

    yielded_sessions: list[AsyncMock] = []
    async for session in db_module.get_db():
        yielded_sessions.append(session)
        # At yield-time the reset must already have run — otherwise the
        # handler's first query (which typically lands before set_tenant)
        # can still hit a stale RLS context.
        assert session.rollback.await_count == 1
        assert session.execute.await_count == 2
        break

    assert yielded_sessions == [fake_session]


@pytest.mark.asyncio
async def test_pin_session_also_clears_stale_guc() -> None:
    """External-session pin (used by provisioning orchestrator) must reset too.

    `pin_session` accepts a session from the caller. Provisioning pipelines
    reuse a long-lived session across compensator steps; each step should
    start from a clean RLS context, not whatever the previous step left
    behind.
    """
    session = _fake_session()
    session.connection = AsyncMock()

    await db_module.pin_session(session)

    # Same contract as _pin_and_reset_connection.
    assert session.connection.await_count == 1
    assert session.rollback.await_count == 1
    assert session.execute.await_count == 2


# ---------------------------------------------------------------------------
# PooledTenantSession — defense-in-depth auto-reset on session-maker enter
# ---------------------------------------------------------------------------
#
# The explicit `_pin_and_reset_connection` calls in `get_db` / `tenant_scoped_session`
# / `pin_session` / `cross_org_session` already clear stale GUCs at checkout.
# `PooledTenantSession.__aenter__` runs the same pin+reset automatically on
# every `async with AsyncSessionLocal() as s:` block, so a future helper that
# forgets to invoke the explicit call cannot re-introduce the 2026-04-24
# pool-pollution bug.


@pytest.mark.asyncio
async def test_pooled_tenant_session_autoresets_on_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AsyncSessionLocal()` yields a session whose connection is pinned AND
    whose tenant GUC has been reset — no explicit caller step required."""
    pin_reset_calls: list[object] = []

    async def fake_pin_and_reset(session: object) -> None:
        pin_reset_calls.append(session)

    monkeypatch.setattr(db_module, "_pin_and_reset_connection", fake_pin_and_reset)

    async with db_module.AsyncSessionLocal() as session:
        # The subclass `__aenter__` must have invoked pin+reset on this exact
        # session instance before yielding.
        assert pin_reset_calls == [session]


def test_pooled_tenant_session_is_the_configured_class() -> None:
    """AsyncSessionLocal must produce instances of PooledTenantSession.

    If a future refactor swaps the `class_=` argument back to the default
    AsyncSession, the auto-reset layer silently disappears and the only
    line of defense becomes the explicit `_pin_and_reset_connection` calls
    — which is exactly the single-point-of-failure we are trying to avoid.
    """
    assert db_module.AsyncSessionLocal.class_ is db_module.PooledTenantSession


@pytest.mark.asyncio
async def test_pooled_tenant_session_closes_on_checkout_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `_pin_and_reset_connection` raises in `__aenter__`, the session MUST
    be closed before the exception propagates. Otherwise the caller never
    enters the `async with` body, `__aexit__` never fires, and the pooled
    connection leaks back to the pool with indeterminate GUC state.
    """

    async def boom(_session: object) -> None:
        raise RuntimeError("simulated pin/reset failure at checkout")

    monkeypatch.setattr(db_module, "_pin_and_reset_connection", boom)

    close_calls: list[object] = []
    orig_close = db_module.AsyncSession.close

    async def tracking_close(self: db_module.AsyncSession) -> None:
        close_calls.append(self)
        await orig_close(self)

    monkeypatch.setattr(db_module.AsyncSession, "close", tracking_close)

    with pytest.raises(RuntimeError, match="simulated pin/reset failure"):
        async with db_module.AsyncSessionLocal() as _:
            # Never reached — checkout raises.
            pass

    # Exactly one session was opened (by super().__aenter__) and it must have
    # been closed by our error-path cleanup. Zero close calls means the
    # connection is leaked with whatever GUC state it had on checkout.
    assert len(close_calls) == 1, f"session must be closed on checkout failure; got {len(close_calls)} close() calls"


# ---------------------------------------------------------------------------
# assert_portal_users_rls_ready — startup fail-loud on broken policy
# ---------------------------------------------------------------------------


def _fake_engine_returning(expr: str | None) -> object:
    class FakeResult:
        def scalar(self) -> str | None:
            return expr

    class FakeConn:
        async def execute(self, _stmt: object) -> FakeResult:
            return FakeResult()

        async def __aenter__(self) -> FakeConn:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> FakeConn:
            return FakeConn()

    return FakeEngine()


@pytest.mark.asyncio
async def test_assert_portal_users_rls_ready_passes_with_is_null_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current production policy uses `NULLIF(...) IS NULL OR ...` form."""
    captured_expr = (
        "((org_id = (NULLIF(current_setting('app.current_org_id'::text, true), "
        "''::text))::integer) OR (NULLIF(current_setting('app.current_org_id'::text, true), "
        "''::text) IS NULL))"
    )
    monkeypatch.setattr(db_module, "engine", _fake_engine_returning(captured_expr))

    # Must not raise.
    await db_module.assert_portal_users_rls_ready()


@pytest.mark.asyncio
async def test_assert_portal_users_rls_ready_raises_when_is_null_branch_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy without IS NULL would 404 every request after deploy — fail at startup."""
    strict_expr = "(org_id = (current_setting('app.current_org_id'::text))::integer)"
    monkeypatch.setattr(db_module, "engine", _fake_engine_returning(strict_expr))

    with pytest.raises(RuntimeError, match="IS NULL"):
        await db_module.assert_portal_users_rls_ready()


@pytest.mark.asyncio
async def test_assert_portal_users_rls_ready_raises_when_policy_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No policy at all → _get_caller_org never returns a row → fail at startup."""
    monkeypatch.setattr(db_module, "engine", _fake_engine_returning(None))

    with pytest.raises(RuntimeError, match="no 'tenant_isolation' policy"):
        await db_module.assert_portal_users_rls_ready()


# ---------------------------------------------------------------------------
# cross_org_session — no more double-reset of app.cross_org_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_session_delegates_reset_to_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross_org_session finally block must use `_reset_tenant_context`.

    Before the fix it had its own standalone
    `set_config('app.cross_org_admin', '', false)` wrapped in suppress —
    same aborted-transaction trap. Now the shared helper handles it.

    Post-2026-04-24 pool-reset fix: the helper is also invoked at checkout
    via `_pin_and_reset_connection`, so a `cross_org_session` block runs
    two resets total (checkout + cleanup). Both target the same fake
    session, and the cleanup one is the one that matters for pool hygiene.
    """
    fake_session = AsyncMock()
    fake_session.rollback = AsyncMock()
    fake_session.execute = AsyncMock(return_value=MagicMock())
    fake_session.connection = AsyncMock()

    class FakeSessionCM:
        async def __aenter__(self) -> AsyncMock:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: FakeSessionCM())

    reset_calls: list[AsyncMock] = []

    async def fake_reset(session: AsyncMock) -> None:
        reset_calls.append(session)

    monkeypatch.setattr(db_module, "_reset_tenant_context", fake_reset)

    async with db_module.cross_org_session() as db:
        assert db is fake_session

    # Two resets: one at checkout (_pin_and_reset_connection), one at cleanup.
    # Both must target the fake session — no other sessions created.
    assert len(reset_calls) == 2, "Shared reset must run at checkout AND cleanup"
    assert all(s is fake_session for s in reset_calls)
