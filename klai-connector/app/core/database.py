"""Async database engine and session factory for klai-connector.

SPEC-SEC-CONNECTOR-RLS-001 — RLS on connector.sync_runs.

PostgreSQL Row-Level Security on ``connector.sync_runs`` requires every
session to have ``app.current_org_id`` (or ``app.cross_org_admin``) set
on its physical connection. Three building blocks live here:

1. ``PooledTenantSession`` — AsyncSession subclass that auto-pins the
   pooled connection AND resets RLS GUCs on ``__aenter__``. Mirrors
   portal-api's pattern.
2. ``tenant_scoped_session(org_id)`` — async context manager for normal
   tenant-bound work. Sets ``app.current_org_id`` to the tenant's
   Zitadel-resourceowner string before yielding.
3. ``cross_org_session()`` — async context manager for the legitimate
   cross-org sites (lifespan crash-recovery, periodic reaper). Sets
   ``app.cross_org_admin = '1'`` so the policy permits SELECT/UPDATE/
   DELETE across all tenants. Logs entry/exit at INFO for audit.

The policy is Category D (strict): empty GUC + no cross_org_admin flag
returns zero rows / blocks INSERT with 42501. That is the loud-failure
mode we chose over silent cross-tenant leakage.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = structlog.get_logger(__name__)

# Module-level references initialised during app lifespan.
engine: AsyncEngine | None = None
session_maker: async_sessionmaker[AsyncSession] | None = None


# ---------------------------------------------------------------------------
# Tenant context — RLS GUC management
# ---------------------------------------------------------------------------


async def _reset_tenant_context(session: AsyncSession) -> None:
    """Clear app.current_org_id and app.cross_org_admin on the session's connection.

    Called before the connection returns to the pool so the next request /
    task that picks it up starts with a clean RLS context.

    Rolls back FIRST. If the session is in an aborted-transaction state (e.g.
    after a 42501 RLS failure from the strict policy), PostgreSQL rejects
    every subsequent command with "current transaction is aborted" — including
    our set_config reset. Without the rollback the suppressed exception path
    would silently leave the leftover tenant context on the pooled connection.
    Mirrors portal-api/backend/app/core/database.py::_reset_tenant_context.
    """
    with contextlib.suppress(Exception):
        await session.rollback()
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.cross_org_admin', '', false)"))


async def _pin_and_reset_connection(session: AsyncSession) -> None:
    """Pin the pooled connection AND clear any stale RLS context.

    Two jobs at checkout:

    1. Pin via ``session.connection()`` so subsequent statements use the
       same physical connection (PostgreSQL ``set_config`` is connection-
       local).
    2. Clear stale ``app.current_org_id`` / ``app.cross_org_admin`` from
       a previous request that may have crashed in mid-cleanup.

    Defense-in-depth at checkout closes the window where a pooled
    connection returns with a leftover tenant GUC and the next request
    silently filters by the wrong tenant.
    """
    await session.connection()
    await _reset_tenant_context(session)


class PooledTenantSession(AsyncSession):
    """AsyncSession subclass that auto-pins + resets RLS GUCs at __aenter__.

    Belt-and-braces with explicit ``_pin_and_reset_connection`` calls in
    helpers below. Direct ``async with session_maker() as s:`` blocks pick
    up the auto-reset for free; helpers call the explicit version so unit
    tests with FakeSession (which bypasses the subclass) stay covered.
    """

    async def __aenter__(self) -> PooledTenantSession:
        result = await super().__aenter__()
        await _pin_and_reset_connection(result)
        return result


async def set_tenant(session: AsyncSession, org_id: str) -> None:
    """Set PostgreSQL session-level tenant context for RLS.

    Uses ``set_config`` with ``is_local=false`` so the setting survives
    commits within the same connection checkout. The session's caller is
    responsible for ensuring the connection is pinned (every helper
    below does so).

    ``org_id`` is the Zitadel-resourceowner string. Empty / whitespace-
    only values are rejected — they would otherwise let the policy's
    ``app.current_org_id = ''`` permissive-by-emptiness branch leak rows
    cross-tenant (which is why we chose Category D in the first place).
    """
    if not org_id or not org_id.strip():
        raise ValueError(
            "set_tenant requires a non-empty org_id. Empty values would "
            "bypass RLS (Category D policy returns zero rows on empty "
            "GUC, but no rows + non-tenant-scoped code is its own bug)."
        )
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": org_id},
    )


async def _set_cross_org_admin(session: AsyncSession) -> None:
    """Mark the session as cross-org-admin for the current connection."""
    await session.execute(text("SELECT set_config('app.cross_org_admin', '1', false)"))


# ---------------------------------------------------------------------------
# Engine init / dispose / dependency injection
# ---------------------------------------------------------------------------


def init_engine(database_url: str) -> AsyncEngine:
    """Create the async engine and session factory.

    Must be called once at application startup.
    """
    global engine, session_maker  # noqa: PLW0603
    engine = create_async_engine(database_url, echo=False, pool_size=10, max_overflow=20)
    session_maker = async_sessionmaker(
        engine,
        class_=PooledTenantSession,
        expire_on_commit=False,
    )
    return engine


async def dispose_engine() -> None:
    """Dispose the engine connection pool. Call at shutdown."""
    global engine, session_maker  # noqa: PLW0603
    if engine is not None:
        await engine.dispose()
        engine = None
        session_maker = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    The session is pinned + reset on entry. CALLERS that operate inside a
    tenant-bound request MUST call ``set_tenant(session, org_id)`` before
    issuing queries against RLS-protected tables — otherwise the strict
    policy returns zero rows.
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


@contextlib.asynccontextmanager
async def tenant_scoped_session(org_id: str) -> AsyncIterator[AsyncSession]:
    """Async context manager for tenant-bound work outside a FastAPI request.

    Used by the scheduler, sync_engine, reaper-when-org-known, and any
    other background path that needs to issue queries on behalf of one
    specific tenant. Pins the connection, sets ``app.current_org_id``,
    yields the session, and clears the GUC on exit.
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        await _pin_and_reset_connection(session)
        try:
            await set_tenant(session, org_id)
            yield session
        finally:
            await _reset_tenant_context(session)


@contextlib.asynccontextmanager
async def cross_org_session() -> AsyncIterator[AsyncSession]:
    """Async context manager for the legitimate cross-org sites.

    Two callers in klai-connector today:
    - Lifespan crash-recovery in app/main.py — resets stuck RUNNING runs
      from a previous crash, no tenant context exists yet.
    - SyncRunReaper.finalise_stuck_runs — periodic sweep across all
      tenants for runs that hung past the deadline.

    Sets ``app.cross_org_admin = '1'`` so the policy's escape branch
    permits SELECT / UPDATE / DELETE across all tenants. Logs entry +
    exit at INFO so any unexpected use shows up in VictoriaLogs queries
    (`service:klai-connector AND event:cross_org_session_*`).

    Adding a third caller? Document the rationale here. Cross-org access
    is the bug-class we are protecting against; every legitimate use
    should be small and auditable.
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    logger.info("cross_org_session_entered")
    async with session_maker() as session:
        await _pin_and_reset_connection(session)
        try:
            await _set_cross_org_admin(session)
            yield session
        finally:
            await _reset_tenant_context(session)
            logger.info("cross_org_session_exited")


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__: list[str] = [
    "PooledTenantSession",
    "cross_org_session",
    "dispose_engine",
    "engine",
    "get_session",
    "init_engine",
    "session_maker",
    "set_tenant",
    "tenant_scoped_session",
]


def __getattr__(name: str) -> Any:  # pragma: no cover - module-level dynamic access
    """Fallback so module-level reads of ``engine`` / ``session_maker`` see the
    current global value rather than the import-time ``None``.
    """
    if name in {"engine", "session_maker"}:
        return globals()[name]
    raise AttributeError(name)
