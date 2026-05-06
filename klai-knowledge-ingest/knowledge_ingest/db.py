"""
asyncpg connection pool for knowledge-ingest.

Uses SQLAlchemy URL parsing to safely extract credentials -- avoids broken URL
parsing when the password contains special chars like +, /, =.

SPEC-TI-003: Added tenant_scoped_connection() context manager for RLS-enabled
queries. Mirrors klai-portal/backend/app/core/database.py set_tenant pattern
adapted for asyncpg pool (no SQLAlchemy session -- raw asyncpg Connection).

SPEC-TI-003-FOLLOWUP-001: Added cross_org_admin_connection() context manager
for startup reapers and deprovision sweeps that legitimately span tenants.
Mirrors klai-connector/app/core/database.py::cross_org_session() but with the
asyncpg.Connection shape used here.
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
#             RLS-protected knowledge.* tables MUST use this helper AND
#             pass the yielded connection down to every query.
#             SPEC-TI-003 AC-9 + SPEC-TI-003-FOLLOWUP-001 AC-1/AC-4.
@asynccontextmanager
async def tenant_scoped_connection(org_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Pin a connection from the pool, set RLS tenant context, yield, then reset.

    SPEC-TI-003 -- RLS Cat-D: knowledge.* tables require ``app.current_org_id``
    GUC to be set on the connection before any DML.

    GUC locality (SPEC-TI-003-FOLLOWUP-001 AC-4)
    ------------------------------------------
    The GUC applies ONLY to queries issued via the YIELDED connection.
    asyncpg's ``set_config(..., is_local=false)`` is session-level, but each
    pool ``acquire()`` returns a different physical connection -- the GUC is
    NOT shared across the pool. So:

      * DO pass ``conn`` down to every helper that issues SQL against
        ``knowledge.*``::

            async with tenant_scoped_connection(org_id) as conn:
                await pg_store.create_artifact(conn, ...)

      * DO NOT rely on "pinning" the GUC by holding the connection open while
        another helper grabs its own pool connection -- that helper's
        connection has no GUC and queries either fail with 42501 (when FORCE
        RLS lands per SPEC-TI-011) or silently return zero rows.

    Pattern mirrors portal-api ``tenant_scoped_session`` in
    ``klai-portal/backend/app/core/database.py`` and connector
    ``tenant_scoped_session`` in
    ``klai-connector/app/core/database.py``. Used for all Procrastinate tasks
    and background workers that touch RLS-protected tables.

    Implementation:

      1. Acquires a dedicated connection from the pool.
      2. Sets ``app.current_org_id = org_id`` via ``set_config`` (``is_local=False``
         so the setting persists for the connection lifetime, not just one
         transaction).
      3. Clears any lingering ``app.cross_org_admin`` bypass.
      4. Yields the pinned connection for caller use.
      5. Resets both GUCs to empty on exit (even on exception) before
         returning the connection to the pool.

    Usage::

        async with tenant_scoped_connection(org_id) as conn:
            await conn.execute("INSERT INTO knowledge.artifacts (...) VALUES (...)")
            await pg_store.create_artifact(conn, ...)  # pass conn down
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Set tenant context -- is_local=False means the GUC persists for the
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


# @MX:ANCHOR: cross-org admin entry point for knowledge-ingest startup/sweeps
# @MX:REASON: Startup reapers, org-wide janitors, and deprovision sweeps need
#             to read/write across tenants without a single org_id. They MUST
#             use this helper instead of the raw pool so RLS policies (once
#             FORCE lands per SPEC-TI-011) recognise the bypass.
#             SPEC-TI-003-FOLLOWUP-001 AC-3.
@asynccontextmanager
async def cross_org_admin_connection() -> AsyncIterator[asyncpg.Connection]:
    """Pin a connection, set the cross-org admin bypass GUC, yield, then reset.

    For startup reapers, org-wide janitors, and deprovision sweeps that
    legitimately span tenants. Mirrors
    ``klai-connector/app/core/database.py::cross_org_session()``.

    The bypass works in concert with the RLS policies: a future
    ``OR current_setting('app.cross_org_admin', true) = 'true'`` clause on the
    ``USING`` filter (planned for SPEC-TI-011 when services move off the
    ``klai`` superuser DSN) reads this GUC and admits the query.

    Until SPEC-TI-011 lands, the GUC is informational -- the ``klai``
    superuser bypasses RLS regardless. Setting it now means audit logs already
    show "this query was bewust admin" instead of "no GUC at all", and the
    RLS policy upgrade in SPEC-TI-011 doesn't need a parallel caller refactor.

    Usage::

        async with cross_org_admin_connection() as conn:
            await conn.execute("DELETE FROM knowledge.crawl_jobs WHERE state = 'stuck'")

    Do NOT use for tenant-scoped work -- use ``tenant_scoped_connection(org_id)``
    instead so per-tenant policies fire correctly.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Clear any lingering tenant context from a prior connection-recycle.
        await conn.execute("SELECT set_config('app.current_org_id', '', false)")
        # Mark this connection as a bewust cross-org admin caller.
        await conn.execute("SELECT set_config('app.cross_org_admin', 'true', false)")
        try:
            yield conn
        finally:
            # Reset both on exit so the connection is clean when returned.
            await conn.execute("SELECT set_config('app.current_org_id', '', false)")
            await conn.execute("SELECT set_config('app.cross_org_admin', 'false', false)")
