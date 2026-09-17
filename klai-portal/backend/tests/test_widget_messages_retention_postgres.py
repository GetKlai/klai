"""Real-PostgreSQL proof that the widget retention sweep survives strict RLS.

Incident (2026-09-16): every run of ``_retention_run_once`` failed with::

    InsufficientPrivilegeError: new row violates row-level security policy
    for table "conversation_quality_judgments"

The anonymizing UPDATEs ran in a ``cross_org_session``. There the policy's
USING clause lets every row through (``_rls_current_org_id()`` is NULL), but
``WITH CHECK (org_id = _rls_current_org_id())`` compares against NULL and
rejects every updated row. The DELETE is not subject to WITH CHECK, which is
why the sweep only broke once a judged conversation expired. A mocked session
cannot express any of this, so these tests use real policies on a non-superuser
role, with the production policy text on the three tables the sweep touches.

Skipped unless ``RLS_TEST_DATABASE_URL`` is set. CI runs them in the postgres
lane (``pytest -m postgres``).

Local run::

    docker run --rm -d --name retention-rls-test -e POSTGRES_PASSWORD=test \
        -p 55441:5432 postgres:16
    RLS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55441/postgres \
        uv run pytest tests/test_widget_messages_retention_postgres.py -m postgres -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
import structlog.testing
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import database as db_module
from app.core.database import TenantContextSession

pytestmark = pytest.mark.postgres

# A dedicated schema keeps the real table names the service queries without
# touching whatever the CI database already has in `public`.
_SCHEMA = "retention_rls_test"
_ROLE = "retention_rls_test_role"
_ROLE_PASSWORD = "retention_rls_test_pw"  # throwaway role in a disposable container

# S608: interpolated values are module constants, not input.
_SETUP_SQL = [
    f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE",
    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') "  # noqa: S608
    f"THEN CREATE ROLE {_ROLE} LOGIN PASSWORD '{_ROLE_PASSWORD}'; END IF; END $$;",
    f"CREATE SCHEMA {_SCHEMA}",
    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {_ROLE}",
    # Same body as production's _rls_current_org_id()
    # (post_deploy_rls_raise_on_missing_context.sql), schema-local so it can
    # never replace the real function in a shared database.
    f"""
    CREATE FUNCTION {_SCHEMA}._rls_current_org_id() RETURNS integer LANGUAGE plpgsql STABLE AS $$
    DECLARE
        v_org    text := current_setting('app.current_org_id', true);
        v_bypass text := current_setting('app.cross_org_admin', true);
    BEGIN
        IF v_bypass = 'true' THEN RETURN NULL; END IF;
        IF v_org IS NULL OR v_org = '' THEN
            RAISE EXCEPTION 'RLS: app.current_org_id is not set' USING ERRCODE = '42501';
        END IF;
        RETURN v_org::integer;
    END;
    $$
    """,
    f"CREATE TABLE {_SCHEMA}.portal_orgs (id integer PRIMARY KEY, widget_messages_retention_days integer)",
    f"""CREATE TABLE {_SCHEMA}.widget_conversations (
        id bigint PRIMARY KEY, org_id integer NOT NULL, visitor_name text, visitor_email text)""",
    f"""CREATE TABLE {_SCHEMA}.widget_messages (
        id bigint PRIMARY KEY,
        conversation_id bigint NOT NULL REFERENCES {_SCHEMA}.widget_conversations(id) ON DELETE CASCADE,
        org_id integer NOT NULL, created_at timestamptz NOT NULL)""",
    f"""CREATE TABLE {_SCHEMA}.conversation_quality_judgments (
        id bigint PRIMARY KEY, org_id integer NOT NULL,
        conversation_id bigint REFERENCES {_SCHEMA}.widget_conversations(id) ON DELETE SET NULL,
        reasoning text, anonymized_at timestamptz)""",
    f"GRANT SELECT ON {_SCHEMA}.portal_orgs TO {_ROLE}",
]
for _table in ("widget_conversations", "widget_messages", "conversation_quality_judgments"):
    _SETUP_SQL += [
        f"ALTER TABLE {_SCHEMA}.{_table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {_SCHEMA}.{_table} FORCE ROW LEVEL SECURITY",
        # Production policy text, verbatim apart from the schema prefix.
        f"CREATE POLICY tenant_isolation ON {_SCHEMA}.{_table} "
        f"USING ({_SCHEMA}._rls_current_org_id() IS NULL OR org_id = {_SCHEMA}._rls_current_org_id()) "
        f"WITH CHECK (org_id = {_SCHEMA}._rls_current_org_id())",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{_table} TO {_ROLE}",
    ]

# Two tenants, each with one conversation: an expired message, a judgment that
# quotes it, and the visitor's contact details. Org 1 also has a fresh message
# that must survive the sweep. Unqualified: the admin engine's search_path is
# the test schema.
_SEED_SQL = [
    "INSERT INTO portal_orgs VALUES (1, NULL), (2, NULL)",
    "INSERT INTO widget_conversations VALUES (10, 1, 'Anna', 'anna@example.com'), (20, 2, 'Bram', 'bram@example.com')",
    "INSERT INTO widget_messages VALUES "
    "(101, 10, 1, now() - interval '30 days'), (102, 10, 1, now()), "
    "(201, 20, 2, now() - interval '30 days')",
    "INSERT INTO conversation_quality_judgments VALUES "
    "(1, 1, 10, 'Anna asked about invoices', NULL), (2, 2, 20, 'Bram asked about refunds', NULL)",
]


def _role_dsn(superuser_dsn: str) -> str:
    scheme, _, rest = superuser_dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_ROLE}:{_ROLE_PASSWORD}@{hostpart}"


@pytest.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """Superuser engine (bypasses RLS) with the seeded schema; the service's
    sessions are swapped for a non-superuser role that RLS does apply to."""
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set — real-PostgreSQL retention tests skipped")
    search_path = {"server_settings": {"search_path": _SCHEMA}}
    admin = create_async_engine(dsn, connect_args=search_path)
    async with admin.begin() as conn:
        for stmt in _SETUP_SQL + _SEED_SQL:
            await conn.execute(text(stmt))

    role_engine = create_async_engine(_role_dsn(dsn), connect_args=search_path)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)
    try:
        with (
            patch.object(db_module, "AsyncSessionLocal", factory),
            patch("app.services.widget_messages_retention.settings") as mock_settings,
        ):
            mock_settings.widget_messages_retention_days = 7
            yield admin
    finally:
        await role_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await admin.dispose()


async def _state(admin: AsyncEngine) -> dict[str, list]:
    async with admin.connect() as conn:
        return {
            "messages": list((await conn.execute(text("SELECT id FROM widget_messages ORDER BY id"))).scalars()),
            "visitors": [
                tuple(r)
                for r in await conn.execute(
                    text("SELECT id, visitor_name, visitor_email FROM widget_conversations ORDER BY id")
                )
            ],
            "reasoning": [
                tuple(r)
                for r in await conn.execute(
                    text(
                        "SELECT conversation_id, reasoning, anonymized_at IS NOT NULL "
                        "FROM conversation_quality_judgments ORDER BY id"
                    )
                )
            ],
        }


async def test_retention_purges_and_anonymizes_expired_conversations_under_strict_rls(
    admin_engine: AsyncEngine,
) -> None:
    from app.services.widget_messages_retention import _retention_run_once

    result = await _retention_run_once()

    assert result["deleted_count"] == 2
    state = await _state(admin_engine)
    assert state["messages"] == [102]
    assert state["visitors"] == [(10, None, None), (20, None, None)]
    assert state["reasoning"] == [(10, None, True), (20, None, True)]


async def test_one_failing_org_neither_blocks_the_others_nor_loses_its_anonymization(
    admin_engine: AsyncEngine,
) -> None:
    """Org 1's anonymization fails in the database; org 2 must still be purged,
    and org 1's expired message must stay until it can be anonymized first."""
    from app.services.widget_messages_retention import _retention_run_once

    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION fail_org_1() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'simulated failure for org 1'; END; $$"
            )
        )
        await conn.execute(
            text(
                "CREATE TRIGGER fail_org_1 BEFORE UPDATE ON conversation_quality_judgments "
                "FOR EACH ROW WHEN (OLD.org_id = 1) EXECUTE FUNCTION fail_org_1()"
            )
        )

    with structlog.testing.capture_logs() as captured:
        result = await _retention_run_once()

    assert result["deleted_count"] == 1
    state = await _state(admin_engine)
    assert state["messages"] == [101, 102]
    assert state["visitors"] == [(10, "Anna", "anna@example.com"), (20, None, None)]
    assert state["reasoning"] == [(10, "Anna asked about invoices", False), (20, None, True)]

    failures = [e for e in captured if e.get("event") == "widget_messages_retention_org_failed"]
    assert [(e["org_id"], e["log_level"]) for e in failures] == [(1, "error")]
