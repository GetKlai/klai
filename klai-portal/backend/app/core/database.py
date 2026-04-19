import contextlib
from collections.abc import AsyncGenerator
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Tracks the current request's org_id so RLS context can be set once per request.
current_org_id: ContextVar[int | None] = ContextVar("current_org_id", default=None)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session with a pinned connection.

    Calling session.connection() at the start pins a single pooled connection
    for the entire session lifetime. This guarantees that set_tenant() and all
    subsequent queries run on the SAME connection — required for PostgreSQL
    session-level set_config() to be visible to RLS policies.

    Without pinning, AsyncSession lazily checks out connections per-statement,
    and the async event loop can hand out different connections for sequential
    awaits. This caused set_tenant() to set app.current_org_id on connection A
    while the next query ran on connection B (where the setting was empty),
    making RLS block all rows.
    """
    async with AsyncSessionLocal() as session:
        # Pin the connection — all queries in this session use the same one.
        await session.connection()
        try:
            yield session
        finally:
            # Reset tenant context before connection returns to the pool.
            # set_config(..., false) = session-level (persists across commits in the same
            # connection checkout). Resetting here ensures the next request gets a clean slate.
            with contextlib.suppress(Exception):
                await session.execute(text("SELECT set_config('app.current_org_id', '', false)"))


async def set_tenant(session: AsyncSession, org_id: int) -> None:
    """Set PostgreSQL session-level tenant context for RLS.

    Uses set_config with is_local=false so the setting survives commits within
    the same connection checkout. get_db() resets it on cleanup.
    Called once per request by _get_caller_org after authentication.
    """
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": str(org_id)},
    )
    current_org_id.set(org_id)
