import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
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


class PooledTenantSession(AsyncSession):
    """AsyncSession that auto-pins + resets RLS tenant context on `__aenter__`.

    Every `async with AsyncSessionLocal() as s:` block starts with:
      1. a pinned pooled connection (session-level `set_config` survives awaits); and
      2. both RLS GUCs (`app.current_org_id`, `app.cross_org_admin`) cleared.

    This is a defense-in-depth layer on top of the explicit
    `_pin_and_reset_connection` calls in `get_db`, `tenant_scoped_session`,
    `pin_session`, `cross_org_session`. A new helper that forgets to call the
    explicit pin+reset would previously re-introduce the 2026-04-24 pool
    pollution bug. With this subclass as the session base, forgetting is
    harmless — every session-maker exit point runs pin+reset unconditionally.

    The explicit `_pin_and_reset_connection` calls stay in place so the
    behaviour remains visible at the call site (and idempotent — a repeat
    reset is three cheap no-op SQL statements).
    """

    async def __aenter__(self) -> AsyncSession:  # type: ignore[override]
        session = await super().__aenter__()
        try:
            await _pin_and_reset_connection(session)
        except BaseException:
            # Pin/reset raised (e.g. asyncpg connection error during pin, or an
            # unsuppressed failure in _reset_tenant_context). The caller never
            # enters the `async with` body, so `__aexit__` does not fire. Close
            # the session explicitly so its pooled connection returns to the
            # pool instead of leaking with indeterminate GUC state. Using
            # BaseException also covers KeyboardInterrupt / SystemExit.
            await session.close()
            raise
        return session


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=PooledTenantSession,
    expire_on_commit=False,
)


async def _pin_and_reset_connection(session: AsyncSession) -> None:
    """Pin the session's pooled connection AND clear any stale tenant context.

    Two jobs, both at checkout time:

    1. Pin the pooled connection via `session.connection()`. After this call
       every subsequent statement on the session uses the same physical
       connection, so PostgreSQL session-level `set_config()` values stay
       visible across awaits.

    2. Clear any stale `app.current_org_id` / `app.cross_org_admin` inherited
       from a prior request. `_reset_tenant_context` already runs at cleanup,
       but its two `set_config` calls are each wrapped in `suppress(Exception)`
       — if the suppressed path fires (aborted transaction, closed connection,
       etc.) the GUC stays set on the pooled connection. The next request
       picking up that connection runs its auth lookup BEFORE set_tenant, so
       a stale GUC from a different tenant silently filters `portal_users` via
       RLS. Observable symptom: valid sessions get intermittent
       "Organisation not found" 404s on `/api/app/*` endpoints, with the
       exact same cookie alternately succeeding and failing within seconds
       depending on which pooled connection is checked out. Defense-in-depth
       at checkout closes that window.
    """
    await session.connection()
    await _reset_tenant_context(session)


async def _reset_tenant_context(session: AsyncSession) -> None:
    """Clear app.current_org_id and app.cross_org_admin on the session's connection.

    Called before the connection returns to the pool so the next request /
    task that picks it up starts with a clean RLS context.

    Rolls back FIRST. If the session is in an aborted-transaction state (e.g.
    after a 42501 RLS failure from the fail-loud policy), PostgreSQL rejects
    every subsequent command with "current transaction is aborted" — including
    our set_config reset. Without the rollback the suppressed exception path
    would silently leave the leftover tenant context on the pooled connection,
    and the next request picking up that connection would see it and silently
    filter rows by the wrong tenant.

    Both GUCs are reset so this helper can be shared between get_db(),
    tenant_scoped_session() and cross_org_session() without duplicating the
    pool-leak guard.
    """
    # Step 1: clear any aborted-transaction state so set_config can run.
    with contextlib.suppress(Exception):
        await session.rollback()
    # Step 2: clear both RLS GUCs. Each in its own suppress so one failure
    # does not skip the other.
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('app.cross_org_admin', '', false)"))


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

    The explicit `_pin_and_reset_connection` below is intentionally double work
    with `PooledTenantSession.__aenter__`. Rationale:
      * Tests monkeypatch `AsyncSessionLocal` with a FakeSession that bypasses
        `PooledTenantSession` entirely, so the explicit call is the only way
        checkout behaviour stays covered in unit tests.
      * The three extra SQL statements per checkout are sub-millisecond and
        the call site makes the invariant readable without chasing a subclass.
      * `_reset_tenant_context` is idempotent — repeating it is cheap and safe.
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


async def set_tenant(session: AsyncSession, org_id: int) -> None:
    """Set PostgreSQL session-level tenant context for RLS.

    Uses set_config with is_local=false so the setting survives commits within
    the same connection checkout. get_db() resets it on cleanup.

    The caller is responsible for ensuring the session's connection is pinned
    (via session.connection() or a pinned dependency). Otherwise the
    SET may land on a different pooled connection than later queries and RLS
    will silently filter rows. Use `tenant_scoped_session()` below if you
    don't already have a pinned session.

    Called once per request by _get_caller_org after authentication.
    """
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": str(org_id)},
    )
    current_org_id.set(org_id)


async def assert_portal_users_rls_ready() -> None:
    """Fail-loud at startup if `portal_users` RLS breaks `_get_caller_org`.

    `_get_caller_org` looks up `portal_users` with a freshly-reset tenant
    GUC (empty string, thanks to `_pin_and_reset_connection` at checkout).
    That only returns the authenticated user's row when the policy includes
    an `IS NULL` branch — i.e. the current `tenant_isolation` expression
    evaluates to TRUE when `app.current_org_id` is NULL/empty.

    If a future migration tightens the policy to the strict form
    `org_id = current_setting(...)::int` (no IS NULL branch), every
    authenticated request would 404 immediately after deploy because the
    auth lookup returns zero rows on the reset connection. Catch that at
    startup, not in the first user's session.

    The check is cheap (one SQL statement) and runs once per process.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT pg_get_expr(p.polqual, p.polrelid) "
                "FROM pg_policy p JOIN pg_class c ON p.polrelid = c.oid "
                "WHERE c.relname = 'portal_users' AND p.polname = 'tenant_isolation'"
            )
        )
        expr = result.scalar()

    if expr is None:
        raise RuntimeError(
            "Startup RLS check: portal_users has no 'tenant_isolation' policy. "
            "_get_caller_org cannot resolve any user. "
            "Re-run migrations or restore the policy."
        )
    if "IS NULL" not in expr:
        raise RuntimeError(
            "Startup RLS check: portal_users 'tenant_isolation' policy is missing "
            "the `IS NULL` branch. The checkout-time GUC reset in "
            "_pin_and_reset_connection would make every _get_caller_org lookup "
            "return zero rows (HTTP 404 'Organisation not found' for every "
            f"authenticated request). Current policy expression: {expr}"
        )


@contextlib.asynccontextmanager
async def tenant_scoped_session(org_id: int) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-aware session for background tasks and fire-and-forget writes.

    Opens a fresh AsyncSession, pins its pooled connection, sets
    app.current_org_id via set_config(), yields for use, and resets the
    tenant context on exit before the connection returns to the pool.

    Use this instead of `async with AsyncSessionLocal() as db` anywhere
    you need to read or write an RLS-protected table outside of a request
    scope — e.g. asyncio.create_task() callbacks, BackgroundTasks, poller
    loops that read one tenant at a time.

    Do NOT use this for cross-tenant operations (meeting dedup across all
    orgs, tenant discovery, etc.); those must intentionally run without
    tenant context.

    Example:
        async def record_event(org_id: int, event: str) -> None:
            async with tenant_scoped_session(org_id) as db:
                db.add(MyModel(...))
                await db.commit()
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        await set_tenant(session, org_id)
        try:
            yield session
        finally:
            await _reset_tenant_context(session)


async def pin_session(session: AsyncSession) -> None:
    """Pin an externally-provided session's pooled connection.

    For code paths that accept a session as a parameter (e.g. provisioning
    orchestrator) and need to guarantee that later set_config() calls on
    that session remain visible. Idempotent — calling session.connection()
    twice is safe, and re-clearing the tenant GUC is a no-op when already
    clear.
    """
    await _pin_and_reset_connection(session)


@contextlib.asynccontextmanager
async def cross_org_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that BYPASSES tenant RLS — for cross-org admin tasks only.

    Sets the PostgreSQL session variable `app.cross_org_admin=true`, which
    the `_rls_current_org_id()` policy function reads to allow SELECT /
    INSERT / UPDATE / DELETE across all tenants. Resets the flag on exit.

    DO NOT USE for anything that processes a single tenant's data. Use
    `tenant_scoped_session(org_id)` for that — it sets the tenant context
    and guarantees RLS enforcement.

    Legitimate use cases (as of 2026-04-21):

      - `bot_poller`: poll ACTIVE / STUCK Vexa meetings across all orgs in
        one pass so missed-webhook recovery covers every tenant.
      - `invite_scheduler`: iCal UID dedup and cancel lookup — UIDs are
        globally unique and we cannot derive the owning org from the
        cancel signal.
      - `connector_credentials` KEK rotation: operator-initiated full sweep
        of `portal_orgs.connector_dek_enc` re-encryption.
      - `recording_cleanup_loop`: SELECT stale meetings across all orgs
        (but the UPDATE that flips recording_deleted MUST use
        `tenant_scoped_session(meeting.org_id)` — already enforced).

    Anything new you add here must have a written @MX:REASON justifying
    why tenant scoping is not possible.
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        await session.execute(text("SELECT set_config('app.cross_org_admin', 'true', false)"))
        try:
            yield session
        finally:
            # _reset_tenant_context rolls back first, then clears BOTH
            # app.current_org_id and app.cross_org_admin in suppressed blocks —
            # so the pool cannot inherit the cross-org bypass flag from a
            # session that aborted before reaching this finally.
            await _reset_tenant_context(session)
