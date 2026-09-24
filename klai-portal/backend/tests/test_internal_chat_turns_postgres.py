"""Real-PostgreSQL proof that an internal-chat turn record stays inside its org.

Same shape as ``test_support_cases_postgres.py``: a throwaway schema, the
fail-loud tenant helper, the table as the Alembic migration creates it, and a
non-superuser role, so RLS applies. The policy is not retyped here: it is read
from the shipped ``post_deploy_i5c6t7u8r9n0_internal_chat_turns_rls.sql`` and
pointed at the throwaway schema, so the test fails when that file's policy
does. The row is written by the production writer, ``record_internal_turn``.

Run: ``RLS_TEST_DATABASE_URL=... uv run pytest tests/test_internal_chat_turns_postgres.py -m postgres -q``

synthetic-data: generator=hand-written seed=0 (org ids 901/902, invented signals).
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import database as db_module
from app.core.database import TenantContextSession, tenant_scoped_session
from app.services.widget_audit import record_internal_turn

pytestmark = pytest.mark.postgres

_SCHEMA = "internal_chat_turns_test_schema"
_ROLE = "internal_chat_turns_test_role"
_PW = "internal_chat_turns_test_pw"  # throwaway role in a disposable schema
_POLICY_SQL = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "post_deploy_i5c6t7u8r9n0_internal_chat_turns_rls.sql"
)


def _shipped_rls_statements() -> list[str]:
    """ENABLE, FORCE and the policy from the post-deploy SQL, aimed at the test schema.

    OWNER TO klai and the grants to portal_api name roles a test database does
    not have; the test role gets its own grants below.
    """
    body = _POLICY_SQL.read_text().split("BEGIN;", 1)[1].split("COMMIT;", 1)[0]
    body = re.sub(r"--[^\n]*", "", body)
    statements = [stmt.strip() for stmt in body.split(";") if stmt.strip()]
    kept = [stmt for stmt in statements if " OWNER TO " not in stmt and not stmt.startswith("GRANT")]
    return [stmt.replace("public.", f"{_SCHEMA}.") for stmt in kept]


_SETUP = [
    f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE",
    # S608: every interpolated value is a module constant, never input.
    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}') "  # noqa: S608
    f"THEN CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PW}'; END IF; END $$;",
    f"CREATE SCHEMA {_SCHEMA}",
    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {_ROLE}",
    # Same fail-loud body as production's public._rls_current_org_id().
    f"""
    CREATE FUNCTION {_SCHEMA}._rls_current_org_id() RETURNS integer LANGUAGE plpgsql STABLE AS $fn$
    DECLARE v_org text := current_setting('app.current_org_id', true);
            v_bypass text := current_setting('app.cross_org_admin', true);
    BEGIN
        IF v_bypass = 'true' THEN RETURN NULL; END IF;
        IF v_org IS NULL OR v_org = '' THEN
            RAISE EXCEPTION 'RLS: app.current_org_id is not set' USING ERRCODE = '42501';
        END IF;
        RETURN v_org::integer;
    END; $fn$
    """,
    f"CREATE TABLE {_SCHEMA}.portal_orgs (id integer PRIMARY KEY)",
    # As alembic/versions/i5c6t7u8r9n0_add_internal_chat_turns.py creates it.
    f"""CREATE TABLE {_SCHEMA}.internal_chat_turns (
        id bigserial PRIMARY KEY,
        org_id integer NOT NULL REFERENCES {_SCHEMA}.portal_orgs(id) ON DELETE CASCADE,
        answer_signals jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""",
    *_shipped_rls_statements(),
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT EXECUTE ON FUNCTION {_SCHEMA}._rls_current_org_id() TO {_ROLE}",
    "INSERT INTO portal_orgs (id) VALUES (901), (902)",
]


def _role_dsn(dsn: str) -> str:
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_ROLE}:{_PW}@{hostpart}"


@pytest.fixture
async def pg() -> AsyncIterator[AsyncEngine]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set")
    search_path = {"server_settings": {"search_path": _SCHEMA}}
    admin = create_async_engine(dsn, connect_args=search_path)
    async with admin.begin() as conn:
        for stmt in _SETUP:
            await conn.execute(text(stmt))
    role_engine = create_async_engine(_role_dsn(dsn), connect_args=search_path)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)
    try:
        with patch.object(db_module, "AsyncSessionLocal", factory):
            yield admin
    finally:
        await role_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await admin.dispose()


async def _visible_signals(org_id: int) -> list[dict]:
    async with tenant_scoped_session(org_id) as db:
        rows = await db.execute(text("SELECT answer_signals FROM internal_chat_turns"))
        return [row[0] for row in rows]


async def test_internal_turn_record_is_readable_only_inside_its_own_org(pg) -> None:
    signals = {"decision": "answer", "band": "high", "sub_questions": 0, "model": "klai-primary"}

    await record_internal_turn(org_id=901, answer_signals=signals)

    assert await _visible_signals(901) == [signals]
    assert await _visible_signals(902) == []
