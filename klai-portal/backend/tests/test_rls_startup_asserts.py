"""Startup fail-loud checks around the RLS tenant-context model.

Renamed from ``test_tenant_context_reset.py`` on 2026-08-13. That file locked the
ordering and GUC coverage of the connection reset/pin helpers in
``app/core/database.py``. All of them were deleted when tenant context collapsed
onto the single per-transaction model: the cleanup-time reset never durably
landed (session close rolls it back), and the checkout-time reset defended
against session-level GUC pollution that portal-api no longer produces.

What survives here is what still has a contract:

  * ``assert_portal_users_rls_ready`` — the lifespan guard that refuses to boot
    when the ``portal_users`` policy loses its ``IS NULL`` branch. The auth
    lookup still runs before ``set_tenant``, with an empty ``app.current_org_id``,
    so that branch is still load-bearing.
  * ``AsyncSessionLocal`` is wired to the tenant session class — without it the
    ``after_begin`` listener never fires and nothing sets the RLS GUCs at all.

Per-transaction behaviour is covered by ``tests/test_rls_txn_context.py`` (unit)
and ``tests/test_rls_txn_context_postgres.py`` (real PostgreSQL).
"""

from __future__ import annotations

import pytest

from app.core import database as db_module

# ---------------------------------------------------------------------------
# Session-class wiring — the listener's only delivery mechanism
# ---------------------------------------------------------------------------


def test_tenant_context_session_is_the_configured_class() -> None:
    """AsyncSessionLocal must produce instances of TenantContextSession.

    If a future refactor swaps the `class_=` argument back to the plain
    AsyncSession, `sync_session_class` reverts to the stock `Session`, the
    `after_begin` listener never fires, and every statement runs with no RLS
    context — Cat-D tables raise 42501, Cat-A tables read cross-tenant.
    """
    assert db_module.AsyncSessionLocal.class_ is db_module.TenantContextSession


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
