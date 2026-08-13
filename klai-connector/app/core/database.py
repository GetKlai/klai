"""Async database engine, session factory, and RLS session helpers for klai-connector.

SPEC-TI-002: adds set_tenant(), tenant_scoped_session(), and cross_org_session()
to support the Cat-D RLS policies deployed by
alembic/versions/post_deploy_008_rls_tenant_isolation.sql.

Session helper design mirrors klai-portal/backend/app/core/database.py.
Key differences for klai-connector:
  - org_id is ``str`` (Zitadel resourceowner / VARCHAR(255)), not ``int``.
  - No AsyncSession subclass (simpler service; no complex auth flow that
    requires auto-pin at checkout). Helpers call _pin_and_reset_connection
    explicitly on every session entry. NOTE: klai-portal has since replaced its
    session-level GUC model with a per-transaction one (an ``after_begin``
    listener); this service still uses the session-level + reset model.
  - The pool is smaller (pool_size=10, max_overflow=20) — fine for a
    background-sync service.

Pool-GUC pollution prevention
------------------------------
PostgreSQL ``set_config('app.current_org_id', ...)`` is session-level and
survives the lifetime of a pooled connection. Without an explicit reset on
return-to-pool, a subsequent checkout inherits the previous tenant's GUC
and RLS silently filters to the wrong tenant.

``_pin_and_reset_connection`` clears both GUCs at checkout time (defence-
in-depth: reset also runs in every helper's ``finally`` block). This matches
the portal-backend pattern documented in
``.claude/rules/klai/projects/portal-backend.md`` (pool-GUC pollution).
"""

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Module-level references initialised during app lifespan.
engine: AsyncEngine | None = None
session_maker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Create the async engine and session factory.

    Must be called once at application startup.

    Args:
        database_url: PostgreSQL connection string (asyncpg driver).

    Returns:
        The newly created ``AsyncEngine``.
    """
    global engine, session_maker  # noqa: PLW0603
    engine = create_async_engine(database_url, echo=False, pool_size=10, max_overflow=20)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def dispose_engine() -> None:
    """Dispose the engine connection pool. Call at shutdown."""
    global engine, session_maker  # noqa: PLW0603
    if engine is not None:
        await engine.dispose()
        engine = None
        session_maker = None


async def _pin_and_reset_connection(session: AsyncSession) -> None:
    """Pin the pooled connection and clear any stale tenant GUCs.

    Two jobs, both at checkout time:

    1. Pin via ``session.connection()`` so that subsequent set_config()
       calls on the session remain on the same physical connection and are
       visible to RLS policies.

    2. Clear ``app.current_org_id`` and ``app.cross_org_admin`` inherited
       from a prior checkout. Without this reset, a connection returned to
       the pool with a stale GUC silently filters or bypasses RLS for the
       next request that picks it up.

    Pool-GUC pollution pitfall reference:
        .claude/rules/klai/projects/portal-backend.md § pool-GUC pollution
    """
    await session.connection()
    await _reset_tenant_context(session)


async def _reset_tenant_context(session: AsyncSession) -> None:
    """Clear both RLS GUCs on the session's connection.

    Rolls back first so that a session in aborted-transaction state (e.g.
    after a 42501 RLS error) can still execute the reset statements.
    Both GUCs are reset in separate suppress() blocks so one failure does
    not skip the other.
    """
    with contextlib.suppress(Exception):
        await session.rollback()
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.cross_org_admin', '', false)"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Pins the pooled connection and resets GUCs at checkout so RLS policies
    start with a clean slate on each request.
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        await _pin_and_reset_connection(session)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


async def set_tenant(session: AsyncSession, org_id: str) -> None:
    """Set PostgreSQL session-level tenant context for RLS.

    Sets ``app.current_org_id`` to ``org_id`` (Zitadel resourceowner string)
    so the _rls_current_org_id() helper function returns it and RLS policies
    on connector.connectors and connector.sync_runs filter to that tenant.

    The session's pooled connection must be pinned before calling this
    (``_pin_and_reset_connection`` does this). Use ``tenant_scoped_session``
    below when you don't already have a pinned session.

    Args:
        session: An already-pinned AsyncSession.
        org_id:  The Zitadel resourceowner string (text/varchar org_id).
    """
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": org_id},
    )


@contextlib.asynccontextmanager
async def tenant_scoped_session(org_id: str) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-scoped session for a single tenant.

    Opens a fresh AsyncSession, pins its pooled connection, sets
    app.current_org_id so the Cat-D RLS policies on connector.connectors
    and connector.sync_runs scope all DML to ``org_id``, then resets on
    exit before the connection returns to the pool.

    Use this for any background task, fire-and-forget write, or scheduler
    loop that processes exactly one tenant's data.

    Do NOT use for cross-tenant operations (reaper sweeps, startup
    recovery). Use ``cross_org_session()`` for those.

    Example::

        async with tenant_scoped_session(org_id) as db:
            db.add(SyncRun(org_id=org_id, ...))
            await db.commit()

    Args:
        org_id: Zitadel resourceowner string (VARCHAR(255)).
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        await _pin_and_reset_connection(session)
        await set_tenant(session, org_id)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


@contextlib.asynccontextmanager
async def cross_org_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that bypasses tenant RLS — for cross-org admin tasks only.

    Sets ``app.cross_org_admin=true`` so _rls_current_org_id() returns NULL
    and the USING branch ``org_id = X OR X IS NULL`` passes for every row.

    Legitimate use cases in klai-connector (SPEC-TI-002):
      - lifespan startup sweep: reset RUNNING sync_runs from a previous crash.
        All tenants' RUNNING rows must be reset, not just one tenant's.
      - SyncRunReaper.tick(): scan all tenants' RUNNING delegated runs.

    NOTE: WITH CHECK on connector tables does NOT have the IS NULL branch.
    INSERT/UPDATE inside a cross_org_session() that sets org_id to a real
    tenant string works correctly. INSERT/UPDATE without an org_id raises
    a 42501 policy violation — this is intentional.

    Do NOT use for per-tenant data processing. Use tenant_scoped_session()
    for that.

    # cross-org-by-design: startup recovery sweep and reaper need all tenants'
    # RUNNING sync_runs; no per-tenant split is possible at that stage.
    # SPEC-TI-002.
    """
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        await _pin_and_reset_connection(session)
        await session.execute(text("SELECT set_config('app.cross_org_admin', 'true', false)"))
        try:
            yield session
        finally:
            await _reset_tenant_context(session)
