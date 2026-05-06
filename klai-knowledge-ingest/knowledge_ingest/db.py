"""
asyncpg connection pool for knowledge-ingest.

Uses SQLAlchemy URL parsing to safely extract credentials -- avoids broken URL
parsing when the password contains special chars like +, /, =.

SPEC-TI-003: Added tenant_scoped_connection() context manager for RLS-enabled
queries. Mirrors klai-portal/backend/app/core/database.py set_tenant pattern
adapted for asyncpg pool (no SQLAlchemy session -- raw asyncpg Connection).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy.engine.url import make_url

from knowledge_ingest.config import settings

_pool: asyncpg.Pool | None = None


def _parse_dsn(dsn: str) -> dict:
    """Extract asyncpg keyword args from a SQLAlchemy DSN string."""
    url = make_url(dsn)
    return {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        kwargs = _parse_dsn(settings.postgres_dsn)
        _pool = await asyncpg.create_pool(**kwargs, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# @MX:ANCHOR: tenant RLS entry point for knowledge-ingest background tasks
# @MX:REASON: Every Procrastinate task + background worker that touches
#             RLS-protected knowledge.* tables MUST use this helper.
#             SPEC-TI-003 AC-9.
@asynccontextmanager
async def tenant_scoped_connection(org_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Pin a connection from the pool, set RLS tenant context, yield, then reset.

    SPEC-TI-003 -- RLS Cat-D: knowledge.* tables require app.current_org_id GUC
    to be set on the connection before any DML. This helper:

      1. Acquires a dedicated connection from the pool (pin -- prevents the GUC
         from bleeding to another coroutine via connection reuse).
      2. Sets app.current_org_id = org_id via set_config (local=False so the
         setting persists for the connection lifetime, not just one transaction).
      3. Yields the pinned connection for caller use.
      4. Resets the GUC to empty and clears app.cross_org_admin on exit (even on
         exception) before returning the connection to the pool.

    Pattern mirrors portal-api tenant_scoped_session in
    klai-portal/backend/app/core/database.py. Must be used for all
    Procrastinate tasks and background workers that touch RLS-protected tables.

    Usage::

        async with tenant_scoped_connection(org_id) as conn:
            await conn.execute("INSERT INTO knowledge.artifacts (...) VALUES (...)")
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Set tenant context -- local=False means the GUC persists for the
        # connection lifetime (not just the current transaction).
        await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
        # Ensure any lingering cross_org_admin bypass is cleared.
        await conn.execute("SELECT set_config('app.cross_org_admin', 'false', false)")
        try:
            yield conn
        finally:
            # Reset on exit so the connection is clean when returned to pool.
            await conn.execute("SELECT set_config('app.current_org_id', '', false)")
            await conn.execute("SELECT set_config('app.cross_org_admin', 'false', false)")
