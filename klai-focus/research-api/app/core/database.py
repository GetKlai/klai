"""Database engine, session factory, and RLS session helpers.

Session helpers (set_tenant, tenant_scoped_session, cross_org_session) mirror
the canonical pattern from klai-portal/backend/app/core/database.py, adapted
for research-api:

- research-api uses tenant_id as UUID (Zitadel zitadel_org_id as UUID).
- GUC name is `app.current_tenant_id` to distinguish from portal-api's
  integer `app.current_org_id`. The RLS helper function research._rls_current_org_id()
  reads `app.current_tenant_id`.
- No PooledTenantSession subclass yet — research-api pool is small and the
  explicit pin+reset in get_db() is sufficient. Add the subclass if
  pool pollution symptoms appear (intermittent 42501 from stale GUC).

SPEC-TI-004-RLS-RESEARCH / Finding A-10.
"""

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.postgres_dsn,
    echo=False,
    connect_args={"server_settings": {"search_path": "research,public"}},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def _reset_tenant_context(session: AsyncSession) -> None:
    """Clear app.current_tenant_id and app.cross_org_admin on the session connection.

    Called before the connection returns to the pool so the next request
    starts with a clean RLS context. Each GUC is reset in its own suppress
    so one failure does not skip the other.
    """
    with contextlib.suppress(Exception):
        await session.rollback()
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.current_tenant_id', '', false)"))
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.cross_org_admin', '', false)"))


async def _pin_and_reset_connection(session: AsyncSession) -> None:
    """Pin the pooled connection and clear stale tenant context.

    Pins via session.connection() so set_config() calls remain on the same
    physical connection (required for RLS GUCs set with is_local=false).
    Resets both GUCs so a recycled connection from a prior request cannot
    leak tenant context into a new request.
    """
    await session.connection()
    await _reset_tenant_context(session)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session with a pinned, RLS-clean connection.

    Use as a FastAPI dependency:
        db: AsyncSession = Depends(get_db)

    After yielding, call set_tenant(db, tenant_id) before the first ORM
    query on any RLS-protected research.* table. The entrypoint is
    auth.get_current_user, which calls set_tenant after resolving the tenant.
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


async def set_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Set PostgreSQL session-level tenant context for research-schema RLS.

    tenant_id is a UUID string (Zitadel zitadel_org_id). The RLS helper
    research._rls_current_org_id() casts it to uuid.

    Must be called AFTER the connection is pinned (get_db() or
    tenant_scoped_session() both pin before yielding). Calling on an
    un-pinned session may silently set the GUC on a different connection.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )


@contextlib.asynccontextmanager
async def tenant_scoped_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-aware session for background tasks or fire-and-forget writes.

    Opens a fresh session, pins its pooled connection, sets app.current_tenant_id,
    yields for use, and resets tenant context on exit.

    Use instead of `async with AsyncSessionLocal() as db` whenever you need to
    read or write RLS-protected research.* tables outside a request scope —
    e.g. asyncio.create_task() callbacks or background workers.

    tenant_id: Zitadel zitadel_org_id (UUID as string).

    Example:
        async def process_notebook(tenant_id: str, notebook_id: str) -> None:
            async with tenant_scoped_session(tenant_id) as db:
                nb = await db.get(Notebook, notebook_id)
                ...
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        await set_tenant(session, tenant_id)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


@contextlib.asynccontextmanager
async def cross_org_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that BYPASSES tenant RLS — for cross-org admin tasks only.

    Sets app.cross_org_admin=true so research._rls_current_org_id() returns NULL,
    which allows the USING branch's IS NULL check to pass for all tenants.

    WITH CHECK does NOT have an IS NULL branch, so INSERT/UPDATE inside a
    cross_org_session() are still rejected by RLS unless the row's tenant_id
    matches a real (non-NULL) value. For writes, use tenant_scoped_session().

    Legitimate uses:
    - Reaper / cleanup sweeps that must scan all tenants
    - Admin analytics queries

    NOT for per-tenant work — always use tenant_scoped_session() for that.
    Inline comment REQUIRED explaining why cross-org access is necessary
    (see standards.md section 4 cross-org-by-design markers).
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        with contextlib.suppress(Exception):
            await session.execute(text("SELECT set_config('app.cross_org_admin', 'true', false)"))
        try:
            yield session
        finally:
            await _reset_tenant_context(session)
