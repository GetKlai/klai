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


@pytest.mark.asyncio
async def test_cross_org_session_delegates_reset_to_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross_org_session finally block must use `_reset_tenant_context`.

    Before the fix it had its own standalone
    `set_config('app.cross_org_admin', '', false)` wrapped in suppress —
    same aborted-transaction trap. Now the shared helper handles it.
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

    assert len(reset_calls) == 1, "Shared reset helper must run exactly once"
    assert reset_calls[0] is fake_session
