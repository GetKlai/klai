import contextlib
import inspect
from collections.abc import AsyncGenerator, AsyncIterator
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import settings

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


class _SyncTenantContextSession(Session):
    """Sync `Session` used underneath `TenantContextSession`.

    Exists purely so the `after_begin` listener below can be registered on a
    class that ONLY portal-api's sessions use. Registering on the stock
    `Session` would also fire for third-party / test-fixture sessions that have
    no tenant contract.
    """


@event.listens_for(_SyncTenantContextSession, "after_begin")
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
        # rollback), so skipping is correct. Mutating the tenant scope INSIDE a
        # savepoint would desync Python state from the database on rollback —
        # `_reject_savepoint_mutation` makes that a loud RuntimeError instead
        # of a silent mismatch.
        return
    if connection.dialect.name != "postgresql":
        # SQLite/in-memory rigs used by some tests have no set_config().
        return
    connection.execute(text(_TENANT_CONTEXT_SQL), _tenant_context_params(session))


def _txn_state(session: AsyncSession, probe: str, default: bool) -> bool:
    """Read a transaction-state probe (`in_transaction` / `in_nested_transaction`).

    Real sessions expose these as SYNC methods returning bool. Mocked sessions
    in tests (AsyncMock) fabricate a coroutine instead; close it so it does not
    surface as a never-awaited RuntimeWarning, and fall back to the default that
    mirrors the request flow (transaction open, not nested).
    """
    fn = getattr(session, probe, None)
    if not callable(fn):
        return default
    state = fn()
    if isinstance(state, bool):
        return state
    if inspect.iscoroutine(state):
        state.close()
    return default


def _reject_savepoint_mutation(session: AsyncSession, mutator: str) -> None:
    """Fail loud when a tenant-scope mutator is called inside a SAVEPOINT.

    PostgreSQL restores `SET LOCAL` values on `ROLLBACK TO SAVEPOINT`. Mutating
    the tenant scope inside a nested transaction therefore desyncs the two
    halves of the model on rollback: PostgreSQL restores the enclosing
    transaction's GUCs while `session.info` / the actor contextvars keep the new
    Python-side value. Subsequent statements in the same transaction would run
    under the OLD database context while the code believes the NEW one applies —
    a silent cross-tenant read. Raise instead.

    No `begin_nested()` call site exists under `app/` today; this guard exists so
    the first one that appears fails immediately rather than subtly.
    """
    if _txn_state(session, "in_nested_transaction", default=False):
        raise RuntimeError(
            f"{mutator}() was called inside a SAVEPOINT (begin_nested). "
            "PostgreSQL restores SET LOCAL values on ROLLBACK TO SAVEPOINT, so the "
            "tenant context would revert in the database while session.info / the "
            "actor contextvars keep the new value — subsequent statements would run "
            "under the wrong tenant. Bind the tenant scope before opening the nested "
            "transaction."
        )


async def _apply_tenant_context(session: AsyncSession) -> None:
    """Apply the four RLS GUCs to the session's ALREADY-OPEN transaction.

    `after_begin` fired for that transaction before the caller mutated
    `session.info` / the actor contextvars, so it ran with the previous state.
    Every mutator (`set_tenant`, `set_request_actor`, `cross_org_scope`) calls
    this immediately afterwards so the current transaction picks the new context
    up without waiting for the next BEGIN.

    When no transaction is open there is nothing to patch — and issuing SQL here
    would open one purely to set GUCs that the next `after_begin` re-declares
    anyway. Returning early keeps a freshly-opened session from checking out a
    pooled connection before it has real work to do.
    """
    if not _txn_state(session, "in_transaction", default=True):
        return  # after_begin applies the context when the next transaction starts
    await session.execute(text(_TENANT_CONTEXT_SQL), _tenant_context_params(session))


class TenantContextSession(AsyncSession):
    """AsyncSession whose every transaction re-declares its own RLS context.

    The entire mechanism is `sync_session_class`: it wires in
    `_SyncTenantContextSession`, whose `after_begin` listener applies the four
    RLS GUCs transaction-locally (`set_config(..., true)`) at the start of EVERY
    transaction, sourced from `session.info` + the actor contextvars.

    Consequences: a post-commit statement (`db.refresh()` right after
    `db.commit()`) is safe on whatever pooled connection it lands on, and no GUC
    can outlive its transaction — pool pollution is structurally impossible
    rather than defended against.
    """

    sync_session_class = _SyncTenantContextSession


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=TenantContextSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield the request-scoped async DB session.

    Nothing to set up and nothing to tear down: tenant context lives on
    `session.info` + the actor contextvars, and every transaction this session
    opens re-declares it transaction-locally via the `after_begin` listener. The
    GUCs vanish at COMMIT/ROLLBACK, so the pooled connection returns clean by
    construction.
    """
    async with AsyncSessionLocal() as session:
        yield session


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
    existed. In the request flow the auth lookup has always opened one; when no
    transaction is open the call is a no-op and the next BEGIN applies the scope.

    Called once per request by `_get_caller_org` / `get_caller` after
    authentication.
    """
    _reject_savepoint_mutation(session, "set_tenant")
    session.info["tenant_org_id"] = org_id
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
    attribute to the same admin. Both GUCs are transaction-local, so nothing has
    to be cleared afterwards — they vanish at COMMIT/ROLLBACK.
    """
    _reject_savepoint_mutation(session, "set_request_actor")
    current_changed_by_user_id.set(changed_by_user_id)
    current_is_platform_admin.set(is_platform_admin)
    await _apply_tenant_context(session)


async def assert_portal_users_rls_ready() -> None:
    """Fail-loud at startup if `portal_users` RLS breaks `_get_caller_org`.

    `_get_caller_org` looks up `portal_users` BEFORE it knows the tenant, so
    that query runs in a transaction whose `after_begin` rendered
    `app.current_org_id` as the empty string. It only returns the authenticated
    user's row when the policy includes an `IS NULL` branch — i.e. the current
    `tenant_isolation` expression evaluates to TRUE when `app.current_org_id`
    is NULL/empty.

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
            "the `IS NULL` branch. The auth lookup runs before set_tenant, with an "
            "empty app.current_org_id, so every _get_caller_org lookup would return "
            "zero rows (HTTP 404 'Organisation not found' for every authenticated "
            f"request). Current policy expression: {expr}"
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

    Opens a fresh AsyncSession and binds the tenant scope via `set_tenant`. From
    there the `after_begin` listener re-declares that scope transaction-locally
    at every BEGIN, so nothing has to be undone on exit.

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
        await set_tenant(session, org_id)
        yield session


@contextlib.asynccontextmanager
async def cross_org_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that BYPASSES tenant RLS — for cross-org admin tasks only.

    Records the bypass on `session.info`; every transaction the session opens
    renders it as `app.cross_org_admin=true`, which the `_rls_current_org_id()`
    policy function reads to allow SELECT / INSERT / UPDATE / DELETE across all
    tenants. The flag is transaction-local, so closing the session ends it.

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
        # Session-scoped flag: the `after_begin` listener re-declares
        # `app.cross_org_admin` transaction-locally on every transaction this
        # session opens, so the bypass survives the commits that multi-step
        # cross-org blocks (invite_scheduler, KEK rotation) perform.
        session.info["cross_org_admin"] = True
        # No-op on a fresh session; covers the case where the caller's factory
        # handed back a session with a transaction already open.
        await _apply_tenant_context(session)
        yield session


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
    _reject_savepoint_mutation(session, "cross_org_scope")
    session.info["cross_org_admin"] = True
    await _apply_tenant_context(session)
    try:
        yield session
    finally:
        session.info["cross_org_admin"] = False
        await _apply_tenant_context(session)
