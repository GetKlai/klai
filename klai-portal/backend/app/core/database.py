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
    # SPEC-PORTAL-PRICING-PER-USER-001 Phase 2: ``klai.changed_by_user_id``
    # is a per-request actor-id GUC consumed by the
    # ``portal_users_seat_history_trg`` trigger. Reset on connection
    # release so the next request never inherits the previous user's
    # identity — a stale value here would attribute a system-write to
    # the wrong admin in the audit trail.
    with contextlib.suppress(Exception):
        await session.execute(text("SELECT set_config('klai.changed_by_user_id', '', false)"))


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


async def assert_partner_api_keys_rls_ready() -> None:
    """Fail-loud at startup if partner_api_keys or partner_api_key_kb_access
    do not have FORCE ROW LEVEL SECURITY enabled at the engine level.

    SPEC-TI-005 / Finding A-3: The original migration b1f2a3c4d5e6 documented
    ENABLE/FORCE in its docstring as an "operator note" rather than in
    executable DDL. This means the klai superuser step may have been skipped,
    leaving partner API keys readable cross-tenant by any portal_api session.
    Partner API keys are bearer-tokens for all customer-API access -- a missed
    FORCE means every key of every tenant is visible to any other tenant's
    session (IF a code path ever queries without an org_id filter).

    Checks pg_class.relrowsecurity (ENABLE) and relforcerowsecurity (FORCE)
    for both tables. Raises RuntimeError if either flag is false so the
    missing post-deploy SQL step surfaces at deploy time, not at first use.

    Operator fix:
        ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
            < klai-portal/backend/alembic/versions/post_deploy_ti005_tenant_isolation_hygiene.sql
    """
    tables = ("partner_api_keys", "partner_api_key_kb_access")
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c "
                "WHERE c.relname = ANY(:tables) AND c.relkind = 'r'"
            ),
            {"tables": list(tables)},
        )
        rows = {row.relname: row for row in result.fetchall()}

    for table in tables:
        row = rows.get(table)
        if row is None:
            raise RuntimeError(
                f"Startup RLS check: table '{table}' not found in pg_class. Was the migration b1f2a3c4d5e6 applied?"
            )
        if not row.relrowsecurity:
            raise RuntimeError(
                f"Startup RLS check (SPEC-TI-005 A-3): '{table}' has "
                "ENABLE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_ti005_tenant_isolation_hygiene.sql as klai superuser "
                "and restart portal-api."
            )
        if not row.relforcerowsecurity:
            raise RuntimeError(
                f"Startup RLS check (SPEC-TI-005 A-3): '{table}' has "
                "FORCE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_ti005_tenant_isolation_hygiene.sql as klai superuser "
                "and restart portal-api."
            )


async def assert_platform_messages_rls_ready() -> None:
    """Fail-loud at startup if platform in-app messaging RLS is incomplete.

    Platform messaging deliberately has asymmetric write rules: platform admins
    create threads/replies through ``app.cross_org_admin=true``; tenant users
    can only read participant-owned threads and insert replies as themselves
    via ``klai.changed_by_user_id``. If the post-deploy RLS SQL is skipped or
    partially applied, the UI turns those policy failures into hard 500s. Catch
    the missing table/policy/FORCE-RLS state before accepting traffic.
    """
    tables = (
        "platform_message_threads",
        "platform_message_participants",
        "platform_messages",
    )
    required_policies = {
        "platform_message_threads": {
            "platform_message_threads_select",
            "platform_message_threads_insert",
            "platform_message_threads_update",
            "platform_message_threads_delete",
        },
        "platform_message_participants": {
            "platform_message_participants_select",
            "platform_message_participants_insert",
            "platform_message_participants_update",
            "platform_message_participants_delete",
        },
        "platform_messages": {
            "platform_messages_select",
            "platform_messages_insert",
            "platform_messages_update",
            "platform_messages_delete",
        },
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "array_agg(p.polname ORDER BY p.polname) FILTER (WHERE p.polname IS NOT NULL) AS policies "
                "FROM pg_class c "
                "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
                "WHERE c.relname = ANY(:tables) AND c.relkind = 'r' "
                "GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity"
            ),
            {"tables": list(tables)},
        )
        rows = {row.relname: row for row in result.fetchall()}

    for table in tables:
        row = rows.get(table)
        if row is None:
            raise RuntimeError(
                f"Startup RLS check: table '{table}' not found. "
                "Apply the platform messaging migration and post-deploy RLS SQL."
            )
        if not row.relrowsecurity:
            raise RuntimeError(
                f"Startup RLS check: '{table}' has ENABLE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_m1n2o3p4q5r6_platform_message_threads_rls.sql and restart portal-api."
            )
        if not row.relforcerowsecurity:
            raise RuntimeError(
                f"Startup RLS check: '{table}' has FORCE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_m1n2o3p4q5r6_platform_message_threads_rls.sql and restart portal-api."
            )
        missing = required_policies[table] - set(row.policies or [])
        if missing:
            raise RuntimeError(
                f"Startup RLS check: '{table}' is missing platform messaging policies: {sorted(missing)}. "
                "Run post_deploy_m1n2o3p4q5r6_platform_message_threads_rls.sql and restart portal-api."
            )


async def assert_product_updates_rls_ready() -> None:
    """Fail-loud at startup if product update read-state RLS is incomplete."""
    tables = (
        "product_updates",
        "product_update_reads",
    )
    required_policies = {
        "product_updates": {
            "product_updates_select",
            "product_updates_insert",
        },
        "product_update_reads": {
            "product_update_reads_select",
            "product_update_reads_insert",
        },
    }
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "array_agg(p.polname ORDER BY p.polname) FILTER (WHERE p.polname IS NOT NULL) AS policies "
                "FROM pg_class c "
                "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
                "WHERE c.relname = ANY(:tables) AND c.relkind = 'r' "
                "GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity"
            ),
            {"tables": list(tables)},
        )
        rows = {row.relname: row for row in result.fetchall()}

    for table in tables:
        row = rows.get(table)
        if row is None:
            raise RuntimeError(
                f"Startup RLS check: table '{table}' not found. "
                "Apply the product updates migration and post-deploy RLS SQL."
            )
        if not row.relrowsecurity:
            raise RuntimeError(
                f"Startup RLS check: '{table}' has ENABLE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_p1r2o3d4u5p6_product_updates_rls.sql and restart portal-api."
            )
        if not row.relforcerowsecurity:
            raise RuntimeError(
                f"Startup RLS check: '{table}' has FORCE ROW LEVEL SECURITY = FALSE. "
                "Run post_deploy_p1r2o3d4u5p6_product_updates_rls.sql and restart portal-api."
            )
        missing = required_policies[table] - set(row.policies or [])
        if missing:
            raise RuntimeError(
                f"Startup RLS check: '{table}' is missing product update policies: {sorted(missing)}. "
                "Run post_deploy_p1r2o3d4u5p6_product_updates_rls.sql and restart portal-api."
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
      - `scripts/create_product_update.py`: @MX:REASON product updates are
        global portal announcements, not tenant-owned rows. Publishing is an
        operator action run from trusted infra, not a customer/admin request.
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
