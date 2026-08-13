import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import settings

# Tracks the current request's org_id so RLS context can be set once per request.
current_org_id: ContextVar[int | None] = ContextVar("current_org_id", default=None)

# Actor identity for the current asyncio task. Task-scoped (contextvar) rather
# than session-scoped because "who is acting" is a property of the request, not
# of a particular DB session: a request that opens a `tenant_scoped_session`
# or `cross_org_session` block must carry the same actor into those sessions.
#
# Feeds `klai.changed_by_user_id`, read by `portal_users_seat_history_trg`.
current_changed_by_user_id: ContextVar[str | None] = ContextVar("current_changed_by_user_id", default=None)
# Feeds `app.is_platform_admin`, read by the tenant_lifecycle_events policies.
current_is_platform_admin: ContextVar[bool] = ContextVar("current_is_platform_admin", default=False)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
)

# One statement, four transaction-local GUCs. `is_local=true` (the third
# set_config argument) is the whole point: the values vanish at COMMIT/ROLLBACK
# so a pooled connection can never carry them into the next request.
_TENANT_CONTEXT_SQL = (
    "SELECT set_config('app.current_org_id', :org_id, true), "
    "set_config('app.cross_org_admin', :cross_org_admin, true), "
    "set_config('klai.changed_by_user_id', :changed_by_user_id, true), "
    "set_config('app.is_platform_admin', :is_platform_admin, true)"
)


def _tenant_context_params(session: Session | AsyncSession) -> dict[str, str]:
    """Render the four RLS GUC values from Python-side state.

    Tenant scope comes from `session.info` (per-session, so a request session
    and a nested `cross_org_session` block cannot contaminate each other).
    Actor identity comes from contextvars (per-task, so it flows into nested
    session blocks opened by the same request).

    Empty string is the "unset" value for every policy — they all wrap the GUC
    in `NULLIF(..., '')`. Values are always rendered, even when everything is
    empty: a deterministic override also neutralises any legacy session-level
    GUC left on the pooled connection by older code or by a manual `psql`
    session.

    Note the two different truthy literals: `app.cross_org_admin` uses `'true'`
    and `app.is_platform_admin` uses `'1'`. Both are pre-existing conventions
    baked into deployed RLS policies — do not "normalise" them.
    """
    # Mocked sessions in tests do not carry a real info dict; a non-dict means
    # no tenant scope (and avoids `.get()` returning a coroutine on AsyncMock).
    info = session.info if isinstance(getattr(session, "info", None), dict) else {}
    org_id = info.get("tenant_org_id")
    return {
        "org_id": str(org_id) if org_id is not None else "",
        "cross_org_admin": "true" if info.get("cross_org_admin") else "",
        "changed_by_user_id": current_changed_by_user_id.get() or "",
        "is_platform_admin": "1" if current_is_platform_admin.get() else "",
    }


class _SyncPooledTenantSession(Session):
    """Sync `Session` used underneath `PooledTenantSession`.

    Exists purely so the `after_begin` listener below can be registered on a
    class that ONLY portal-api's pooled sessions use. Registering on the stock
    `Session` would also fire for third-party / test-fixture sessions that have
    no tenant contract.
    """


@event.listens_for(_SyncPooledTenantSession, "after_begin")
def _apply_tenant_context_on_begin(
    session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """Re-apply the four RLS GUCs transaction-locally at every BEGIN.

    This is the invariant that makes post-commit queries safe: `commit()`
    releases the pooled connection, and the next statement checks one out again
    with no tenant guarantee. Because every transaction begins by re-declaring
    its own context, it does not matter which physical connection it lands on
    or what the previous tenant left behind.

    SQLAlchemy docs require emitting SQL through the supplied `Connection` here,
    not through the `Session` — the session is mid-begin and re-entering it
    would recurse.
    """
    if transaction.nested:
        # SAVEPOINT: inherits the enclosing transaction's GUCs, so there is
        # nothing to establish. PostgreSQL DOES restore SET LOCAL values on
        # ROLLBACK TO SAVEPOINT (verified: 33 before, 22 set inside, 33 after
        # rollback). That makes skipping correct today, and also flags a latent
        # mismatch: if a mutator changed session.info / the actor contextvars
        # INSIDE a nested transaction that later rolls back, PostgreSQL would
        # restore the old GUC while the Python-side state keeps the new value.
        # The mismatch heals at the next top-level BEGIN. No `begin_nested()`
        # call site exists under app/ today, so this is latent, not live.
        return
    if connection.dialect.name != "postgresql":
        # SQLite/in-memory rigs used by some tests have no set_config().
        return
    connection.execute(text(_TENANT_CONTEXT_SQL), _tenant_context_params(session))


async def _apply_tenant_context(session: AsyncSession) -> None:
    """Apply the four RLS GUCs to the session's ALREADY-OPEN transaction.

    `after_begin` fired for that transaction before the caller mutated
    `session.info` / the actor contextvars, so it ran with the previous state.
    Every mutator (`set_tenant`, `set_request_actor`, `cross_org_session`) calls
    this immediately afterwards so the current transaction picks the new context
    up without waiting for the next BEGIN.
    """
    await session.execute(text(_TENANT_CONTEXT_SQL), _tenant_context_params(session))


class PooledTenantSession(AsyncSession):
    """AsyncSession that auto-pins + resets RLS tenant context on `__aenter__`.

    Every `async with AsyncSessionLocal() as s:` block starts with:
      1. a pinned pooled connection; and
      2. all RLS GUCs cleared on that connection.

    The authoritative mechanism is `sync_session_class`: it wires in
    `_SyncPooledTenantSession`, whose `after_begin` listener re-applies the
    tenant context transaction-locally at the start of EVERY transaction. That
    is what makes post-commit statements (`db.refresh()` after `db.commit()`)
    safe regardless of which pooled connection they land on.

    The pin + checkout reset below stays as defense-in-depth: it neutralises any
    session-level GUC that older code, a manual `psql` session, or a future
    regression might leave on a pooled connection.
    """

    sync_session_class = _SyncPooledTenantSession

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

    1. Pin the pooled connection via `session.connection()`. Tenant context no
       longer depends on this (it is re-applied per transaction by the
       `after_begin` listener), but pinning keeps a session's statements on one
       physical connection, which keeps advisory locks and `SELECT … FOR UPDATE`
       semantics predictable across awaits.

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

    Since tenant context became transaction-local (`after_begin` listener),
    portal-api writes no session-level GUCs at all and this reset is pure
    defense-in-depth: it neutralises anything a manual `psql` session, a legacy
    deployment, or a future regression might leave behind. Keep it.

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

    Tenant context is carried by the session (`session.info`) and re-declared
    transaction-locally at every BEGIN by the `after_begin` listener, so RLS no
    longer depends on which pooled connection a statement lands on. Pinning via
    `session.connection()` remains for lock/transaction predictability.

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
    """Bind this session's tenant scope for RLS.

    The scope is stored on `session.info` — Python-side state, not a
    connection-level GUC. From here on, EVERY transaction this session opens
    re-declares `app.current_org_id` transaction-locally via the `after_begin`
    listener, so the context follows the session across commits and across
    pooled-connection checkouts. That is what makes `db.refresh()` immediately
    after `db.commit()` safe.

    `session.info` (rather than a contextvar) is deliberate: a request session
    and a `tenant_scoped_session` / `cross_org_session` block opened inside the
    same asyncio task can hold different scopes without contaminating each
    other.

    The immediate `_apply_tenant_context` call covers the transaction that is
    already open right now — `after_begin` fired for it before this scope
    existed.

    Called once per request by `_get_caller_org` / `get_caller` after
    authentication.
    """
    session.info["tenant_org_id"] = org_id
    current_org_id.set(org_id)
    await _apply_tenant_context(session)


async def set_request_actor(
    session: AsyncSession,
    changed_by_user_id: str | None,
    is_platform_admin: bool,
) -> None:
    """Bind the acting identity for this asyncio task.

    Feeds two GUCs:
      * `klai.changed_by_user_id` — read by `portal_users_seat_history_trg` to
        record WHO changed a seat. Empty means "no acting admin" (signup flows,
        internal callers), which the trigger stores as NULL.
      * `app.is_platform_admin` — unlocks the platform-admin branch of the
        `tenant_lifecycle_events` policies.

    Contextvars (not `session.info`) because identity is a property of the task:
    a background write opened via `tenant_scoped_session` inside a request must
    attribute to the same admin. `_reset_tenant_context` still clears the GUCs
    on the connection as defense-in-depth.
    """
    current_changed_by_user_id.set(changed_by_user_id)
    current_is_platform_admin.set(is_platform_admin)
    await _apply_tenant_context(session)


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

    Opens a fresh AsyncSession, pins its pooled connection, binds the tenant
    scope via `set_tenant` (re-applied transaction-locally at every BEGIN),
    yields for use, and resets the connection's tenant context on exit before
    it returns to the pool.

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
    orchestrator) and want a single physical connection for the duration plus a
    clean starting RLS context. Idempotent — calling session.connection() twice
    is safe, and re-clearing the tenant GUC is a no-op when already clear.
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

    Do NOT call this from a handler that already holds a session (webhook
    handlers, FastAPI routes with `Depends(get_db)`) — it checks out a SECOND
    pooled connection while the first is still held, so a burst of concurrent
    requests can exhaust the pool. Use `cross_org_scope(session)` there.
    """
    async with AsyncSessionLocal() as session:
        await _pin_and_reset_connection(session)
        # Session-scoped flag: the `after_begin` listener re-declares
        # `app.cross_org_admin` transaction-locally on every transaction this
        # session opens, so the bypass survives the commits that multi-step
        # cross-org blocks (invite_scheduler, KEK rotation) perform.
        session.info["cross_org_admin"] = True
        await _apply_tenant_context(session)
        try:
            yield session
        finally:
            # _reset_tenant_context rolls back first, then clears BOTH
            # app.current_org_id and app.cross_org_admin in suppressed blocks —
            # so the pool cannot inherit the cross-org bypass flag from a
            # session that aborted before reaching this finally.
            await _reset_tenant_context(session)


@contextlib.asynccontextmanager
async def cross_org_scope(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Temporarily enable the cross-org RLS bypass on an EXISTING session.

    Unlike `cross_org_session()` this does NOT check out a second pooled
    connection — use it when the caller already holds a session (webhook
    handlers, any route with `Depends(get_db)`). Opening a nested
    `cross_org_session()` there doubles connection usage per request; a burst
    can exhaust the pool while each outer session still holds its connection.

    Safe only because tenant context is transaction-local: the flag lives in
    `session.info` and is applied via `set_config(..., true)`, so no
    session-level GUC can leak onto the pooled connection. The `finally`
    clears the flag and re-applies immediately, so the bypass cannot outlive
    the block even if the body raises.

    Scope it as tightly as possible — ideally one lookup. Everything inside
    sees ALL tenants' rows.
    """
    session.info["cross_org_admin"] = True
    await _apply_tenant_context(session)
    try:
        yield session
    finally:
        session.info["cross_org_admin"] = False
        await _apply_tenant_context(session)
