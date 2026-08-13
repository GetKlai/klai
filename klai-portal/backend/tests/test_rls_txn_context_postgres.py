"""Real-PostgreSQL regression tests for the per-transaction RLS context invariant.

Incident (2026-08-13): ``PATCH /api/app/knowledge-bases/{kb}/connectors/{id}``
returned 500 with::

    sqlalchemy.exc.InvalidRequestError: Could not refresh instance '<PortalConnector>'

raised by ``await db.refresh(connector)`` immediately after ``await db.commit()``.

Root cause: portal-api used to set the RLS GUCs *session-level*
(``set_config(..., is_local=false)``). ``commit()`` releases the pooled
connection; the very next statement (the refresh) implicitly checks a
connection out again with no tenant-context guarantee. A pooled connection
carries whatever GUC the last COMMITTED transaction on it left behind — the old
cleanup reset ran inside a transaction that is rolled back at session close, so
it never durably landed (which is why it was deleted). A connection
polluted with a *different* org's GUC makes the freshly-updated row invisible
under the policy ``org_id = GUC OR GUC IS NULL`` → refresh finds zero rows →
500.

Structural fix: tenant context is a property of the TRANSACTION, not the
connection. The ``after_begin`` listener on ``_SyncTenantContextSession`` applies
all four RLS GUCs transaction-locally (``is_local=true``) from Python-side state
at the start of every transaction. Pool pollution becomes structurally
impossible (transaction-local GUCs vanish at commit/rollback) and every
post-commit statement automatically re-establishes the correct context on
whatever pooled connection it lands on.

These tests exercise a REAL PostgreSQL with REAL RLS policies and a REAL
non-superuser role. They are skipped unless ``RLS_TEST_DATABASE_URL`` is set.

Local run::

    docker run --rm -d --name rls-txn-test -e POSTGRES_PASSWORD=test \
        -p 55439:5432 postgres:16
    RLS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55439/postgres \
        uv run pytest tests/test_rls_txn_context_postgres.py -m postgres -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import Integer, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core import database as db_module
from app.core.database import TenantContextSession, set_tenant

pytestmark = pytest.mark.postgres

_TEST_ROLE = "rls_txn_test_role"
_TEST_ROLE_PASSWORD = "rls_txn_test_pw"  # throwaway role in a disposable container
_TENANT_TABLE = "rls_txn_test_items"
_STRICT_TABLE = "rls_txn_test_strict_items"


class _Base(DeclarativeBase):
    """Local declarative base — deliberately NOT the app's Base.

    The app's metadata carries every portal table; binding it here would make
    this fixture's create/drop cycle touch tables the test does not own.
    """


class RlsTxnTestItem(_Base):
    """Cat-A shaped table: permissive ``IS NULL`` branch, like ``portal_users``."""

    __tablename__ = _TENANT_TABLE

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class RlsTxnStrictItem(_Base):
    """Cat-D shaped table: no permissive branch; cross-org needs the bypass flag."""

    __tablename__ = _STRICT_TABLE

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


def _superuser_dsn() -> str:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set — real-PostgreSQL RLS tests skipped")
    return dsn


def _role_dsn(superuser_dsn: str) -> str:
    """Rewrite the superuser DSN's userinfo to the non-superuser test role.

    Table owners bypass RLS unless FORCE ROW LEVEL SECURITY is set; even then,
    connecting as a dedicated non-owner role is what portal_api actually does.
    """
    scheme, _, rest = superuser_dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_TEST_ROLE}:{_TEST_ROLE_PASSWORD}@{hostpart}"


# S608: the interpolated values are module-level constants in this file, not
# user input — the rule cannot see that through the f-string.
_CREATE_ROLE_SQL = f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_TEST_ROLE}') THEN CREATE ROLE {_TEST_ROLE} LOGIN PASSWORD '{_TEST_ROLE_PASSWORD}'; END IF; END $$;"  # noqa: S608

# Cat-A shape (portal_users): inline NULLIF with a permissive IS NULL branch.
_TENANT_USING = (
    "org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer "
    "OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL"
)
_TENANT_WITH_CHECK = "org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer"

# Cat-D shape: calls the fail-loud helper. Mirrors production's
# `_rls_current_org_id()` from alembic/versions/post_deploy_rls_raise_on_missing_context.sql
# — same three branches (bypass -> NULL, missing context -> RAISE 42501, else
# the org id) and the same STABLE plpgsql body. Renamed so it can never collide
# with the real function in a shared CI database (Postgres has no return-type
# overloading; a same-name/different-signature CREATE would fail).
_STRICT_HELPER_SQL = """
CREATE OR REPLACE FUNCTION _rls_txn_test_org_id()
    RETURNS integer
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_org     text := current_setting('app.current_org_id', true);
    v_bypass  text := current_setting('app.cross_org_admin', true);
BEGIN
    IF v_bypass = 'true' THEN
        RETURN NULL;
    END IF;

    IF v_org IS NULL OR v_org = '' THEN
        RAISE EXCEPTION
            'RLS: app.current_org_id is not set and app.cross_org_admin is not true.'
            USING ERRCODE = '42501';
    END IF;

    RETURN v_org::integer;
END;
$$;
"""
# Prod omits WITH CHECK on these policies, so it defaults to the USING clause.
_STRICT_USING = "_rls_txn_test_org_id() IS NULL OR org_id = _rls_txn_test_org_id()"


def _schema_statements() -> list[str]:
    stmts: list[str] = [
        _CREATE_ROLE_SQL,
        _STRICT_HELPER_SQL,
        f"GRANT EXECUTE ON FUNCTION _rls_txn_test_org_id() TO {_TEST_ROLE}",
    ]
    policies = {
        _TENANT_TABLE: f"USING ({_TENANT_USING}) WITH CHECK ({_TENANT_WITH_CHECK})",
        _STRICT_TABLE: f"USING ({_STRICT_USING})",
    }
    for table, policy in policies.items():
        stmts += [
            f"DROP TABLE IF EXISTS {table} CASCADE",
            f"CREATE TABLE {table} (id serial PRIMARY KEY, org_id integer NOT NULL, name text)",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} {policy}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_TEST_ROLE}",
            f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO {_TEST_ROLE}",
        ]
    return stmts


@pytest_asyncio.fixture
async def rls_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Throwaway RLS-protected tables plus a session factory bound to a
    non-superuser role, using the app's real ``TenantContextSession`` class.

    ``pool_size=1, max_overflow=0`` guarantees every session in a test reuses
    the SAME physical connection — which is what makes pool-pollution
    observable at all.
    """
    superuser_dsn = _superuser_dsn()
    admin_engine = create_async_engine(superuser_dsn)

    async with admin_engine.begin() as conn:
        for stmt in _schema_statements():
            await conn.execute(text(stmt))

    role_engine = create_async_engine(_role_dsn(superuser_dsn), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)

    try:
        yield factory
    finally:
        await role_engine.dispose()
        async with admin_engine.begin() as conn:
            for table in (_TENANT_TABLE, _STRICT_TABLE):
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        await admin_engine.dispose()


async def _current_org_guc(session: AsyncSession) -> str | None:
    result = await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
    return result.scalar()


# ---------------------------------------------------------------------------
# Test A — the incident repro
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_after_commit_survives_pool_pollution(
    rls_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """``session.refresh()`` after ``commit()`` MUST see the row it just wrote.

    The pollution simulated here (``set_config(..., false)`` for a FOREIGN org)
    is exactly what a pooled connection carried in production: a previous
    request's session-level GUC, still committed on the physical connection.

    Old behaviour: the refresh's implicitly-begun transaction inherits
    ``app.current_org_id='22'`` from the connection, the org-8 row fails the
    USING clause, zero rows come back, SQLAlchemy raises
    ``InvalidRequestError: Could not refresh instance``.

    New behaviour: ``after_begin`` re-applies ``app.current_org_id='8'``
    transaction-locally at the start of the refresh's transaction, overriding
    the session-level pollution for the duration of that transaction.
    """
    async with rls_sessionmaker() as session:
        await set_tenant(session, 8)

        item = RlsTxnTestItem(org_id=8, name="incident-repro")
        session.add(item)
        await session.commit()

        # Simulate pool pollution: a foreign request's session-level GUC that
        # survived (was committed) on this physical connection.
        await session.execute(text("SELECT set_config('app.current_org_id', '22', false)"))
        await session.commit()

        # THE incident line — portal-api connectors.py:802 equivalent.
        await session.refresh(item)

        assert item.name == "incident-repro"

        # The transaction that served the refresh must have run with the
        # session's own tenant context, not the polluted '22'.
        assert await _current_org_guc(session) == "8"


# ---------------------------------------------------------------------------
# Test B — the new code writes no durable pollution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_context_leaves_no_durable_pool_pollution(
    rls_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """After a full set_tenant + commit + close cycle, the pooled connection
    must carry NO session-level ``app.current_org_id``.

    The assertion reads the GUC over a RAW engine connection, bypassing
    ``TenantContextSession`` entirely. Observing through a fresh pooled session
    would be green by construction: its ``__aenter__`` reset clears the GUC
    before the assertion could see it. With ``pool_size=1`` the raw checkout
    returns the exact physical connection the tenant session just used — under
    the OLD (session-level ``set_config``) code this read shows the committed
    ``'8'``, which is what makes the test honest.
    """
    async with rls_sessionmaker() as session:
        await set_tenant(session, 8)
        session.add(RlsTxnTestItem(org_id=8, name="pollution-check"))
        await session.commit()

    engine = rls_sessionmaker.kw["bind"]
    async with engine.connect() as raw:
        leftover = (await raw.execute(text("SELECT current_setting('app.current_org_id', true)"))).scalar()
    assert leftover in ("", None), f"pooled connection carried a stale tenant GUC: {leftover!r}"


# ---------------------------------------------------------------------------
# Test C — cross-org bypass survives a commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_flag_survives_commit(
    rls_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A cross-org session must still see all tenants' rows AFTER a commit.

    ``_STRICT_TABLE`` has no permissive ``IS NULL`` branch — without the
    ``app.cross_org_admin`` flag a session sees zero rows. That makes this a
    real assertion rather than a vacuous one.

    Pins the invariant that multi-commit cross-org blocks depend on
    (``invite_scheduler``, platform-admin sweeps). Under the old model the
    session-level flag happened to survive because the connection was pinned;
    under the new model ``after_begin`` re-applies it on every transaction.
    """
    async with rls_sessionmaker() as session:
        await set_tenant(session, 8)
        session.add(RlsTxnStrictItem(org_id=8, name="org8-row"))
        await session.commit()

    async with rls_sessionmaker() as session:
        await set_tenant(session, 22)
        session.add(RlsTxnStrictItem(org_id=22, name="org22-row"))
        await session.commit()

    async with rls_sessionmaker() as session:
        # Same state transition `cross_org_session()` performs.
        session.info["cross_org_admin"] = True
        await db_module._apply_tenant_context(session)

        first = (await session.execute(select(func.count()).select_from(RlsTxnStrictItem))).scalar()
        assert first is not None and first >= 2, f"cross-org session saw only {first} rows"

        await session.commit()

        second = (await session.execute(select(func.count()).select_from(RlsTxnStrictItem))).scalar()
        assert second == first, f"cross-org visibility regressed after commit: {first} before, {second} after"


# ---------------------------------------------------------------------------
# Test D — sequential tenants on ONE physical connection, no reset machinery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_tenant_sessions_reuse_one_connection_without_leaking(
    rls_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Org 22 must not see org 8's rows on the connection org 8 just released.

    This is the invariant the deleted reset/pin machinery used to claim. With
    ``pool_size=1, max_overflow=0`` the second session is guaranteed to check out
    the exact physical connection the first one used, and nothing runs in between
    — no checkout reset, no cleanup reset, no pin. Isolation is carried entirely
    by ``after_begin`` re-declaring the context at every BEGIN.

    Both table shapes are asserted because they fail differently:
      * Cat-A (permissive ``IS NULL`` branch) would leak org-8 rows into org 22's
        result set — a silent cross-tenant READ.
      * Cat-D (fail-loud helper) would either leak or raise 42501 depending on
        which stale value survived.
    """
    async with rls_sessionmaker() as session:
        await set_tenant(session, 8)
        session.add(RlsTxnTestItem(org_id=8, name="org8-cat-a"))
        session.add(RlsTxnStrictItem(org_id=8, name="org8-cat-d"))
        await session.commit()

    async with rls_sessionmaker() as session:
        await set_tenant(session, 22)
        session.add(RlsTxnTestItem(org_id=22, name="org22-cat-a"))
        session.add(RlsTxnStrictItem(org_id=22, name="org22-cat-d"))
        await session.commit()

        cat_a = (await session.execute(select(RlsTxnTestItem.org_id, RlsTxnTestItem.name))).all()
        assert [row.org_id for row in cat_a] == [22], f"org 22 saw foreign Cat-A rows: {cat_a}"

        cat_d = (await session.execute(select(RlsTxnStrictItem.org_id, RlsTxnStrictItem.name))).all()
        assert [row.org_id for row in cat_d] == [22], f"org 22 saw foreign Cat-D rows: {cat_d}"

        # The reads above ran post-commit — i.e. in a transaction that began
        # after the pooled connection was released and re-acquired.
        assert await _current_org_guc(session) == "22"
