"""Real-PostgreSQL proof that an internal conversation's stored case stays inside its org and does not pile up.

Same shape as ``test_internal_chat_turns_postgres.py``: a throwaway schema, the
fail-loud tenant helper, the table as the Alembic migration creates it, and a
non-superuser role, so RLS applies. The policy is read from the shipped
``post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql``, so the test fails
when that file's policy does, the ``user_id`` column is added by the shipped
``post_deploy_a7d2e9c4f1b8_internal_chat_cases_user_id.sql``, and the key moves
to LibreChat's conversation id through the shipped
``post_deploy_f9b2d6a4c8e1_internal_chat_cases_conversation_id.sql``, run
twice as every deploy does. Rows are written, read, forgotten and pruned by
the production functions.

Run: ``RLS_TEST_DATABASE_URL=... uv run pytest tests/test_internal_chat_cases_postgres.py -m postgres -q``

synthetic-data: generator=hand-written seed=0 (org ids 900/901/902, invented ids, case and passage).
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
from app.services.widget_audit import (
    forget_internal_cases,
    keep_internal_case,
    last_internal_case,
    prune_internal_cases,
)

pytestmark = pytest.mark.postgres

_SCHEMA = "internal_chat_cases_test_schema"
_ROLE = "internal_chat_cases_test_role"
_PW = "internal_chat_cases_test_pw"  # throwaway role in a disposable schema
_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_POLICY_SQL = _VERSIONS / "post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql"
_USER_ID_SQL = _VERSIONS / "post_deploy_a7d2e9c4f1b8_internal_chat_cases_user_id.sql"
_CONVERSATION_ID_SQL = _VERSIONS / "post_deploy_f9b2d6a4c8e1_internal_chat_cases_conversation_id.sql"
_CASE = {"problem": "verlof aanvragen", "language": "nl"}
_PASSAGE = {"chunk_id": "c1", "kb_slug": "handboek", "title": "Verlof", "source_url": None, "text": "Verlof via HR."}


def _shipped_statements(path: Path) -> list[str]:
    """The statements of a post-deploy SQL file, aimed at the test schema; ownership and grants left out."""
    body = path.read_text().split("BEGIN;", 1)[1].split("COMMIT;", 1)[0]
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
    # Only what the nightly prune joins on; the real table is wider and Cat-A.
    f"CREATE TABLE {_SCHEMA}.portal_users (org_id integer, zitadel_user_id text, status text)",
    # As alembic/versions/f3a9c6e2b4d1_add_internal_chat_cases.py creates it.
    f"""CREATE TABLE {_SCHEMA}.internal_chat_cases (
        id bigserial PRIMARY KEY,
        org_id integer NOT NULL REFERENCES {_SCHEMA}.portal_orgs(id) ON DELETE CASCADE,
        conversation_key varchar(64) NOT NULL,
        turn_digest varchar(64) NOT NULL,
        case_file jsonb,
        kept_passages jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_internal_chat_cases_org_key UNIQUE (org_id, conversation_key))""",
    *_shipped_statements(_POLICY_SQL),
    *_shipped_statements(_USER_ID_SQL),
    # A row keyed by digests, as the code before the conversation id stored it: the next file deletes it.
    "INSERT INTO portal_orgs (id) VALUES (900)",
    "INSERT INTO internal_chat_cases (org_id, user_id, conversation_key, turn_digest, kept_passages) "
    "VALUES (900, 'u0', 'digest-key', 'digest', '[]')",
    *_shipped_statements(_CONVERSATION_ID_SQL),
    # Deploys apply every post-deploy file again; the second run must change nothing.
    *_shipped_statements(_CONVERSATION_ID_SQL),
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


async def _visible_keys(org_id: int) -> list[str]:
    async with tenant_scoped_session(org_id) as db:
        rows = await db.execute(text("SELECT conversation_id FROM internal_chat_cases ORDER BY conversation_id"))
        return [row[0] for row in rows]


async def _keep(org_id: int, user_id: str, conversation_id: str, **fields) -> None:
    await keep_internal_case(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        case=fields.get("case", _CASE),
        kept_passages=fields.get("kept_passages", [_PASSAGE]),
    )


async def test_the_post_deploy_sql_deletes_the_rows_keyed_by_digests(pg) -> None:
    async with pg.begin() as conn:
        left = (await conn.execute(text("SELECT count(*) FROM internal_chat_cases WHERE org_id = 900"))).scalar_one()

    assert left == 0


async def test_a_stored_case_is_readable_only_inside_its_own_org(pg) -> None:
    await _keep(901, "u1", "k1")

    stored = await last_internal_case(org_id=901, user_id="u1", conversation_id="k1")
    assert stored is not None
    assert (stored.case, stored.kept_passages) == (_CASE, [_PASSAGE])
    assert await _visible_keys(901) == ["k1"]
    assert await _visible_keys(902) == []


async def test_a_conversation_keeps_one_row_with_its_latest_turn(pg) -> None:
    await _keep(901, "u1", "k1", kept_passages=[])
    await _keep(901, "u1", "k1", case=None)

    assert await _visible_keys(901) == ["k1"]
    stored = await last_internal_case(org_id=901, user_id="u1", conversation_id="k1")
    assert stored is not None
    assert (stored.case, stored.kept_passages) == (None, [_PASSAGE])


async def test_a_turn_with_nothing_to_keep_clears_the_conversation_s_case(pg) -> None:
    await _keep(901, "u1", "k1")
    await _keep(901, "u1", "k2")

    await _keep(901, "u1", "k1", case=None, kept_passages=[])

    assert await last_internal_case(org_id=901, user_id="u1", conversation_id="k1") is None
    assert await _visible_keys(901) == ["k2"]


async def test_a_conversation_id_reads_only_the_employee_s_own_case(pg) -> None:
    await _keep(901, "u1", "k1")

    assert await last_internal_case(org_id=901, user_id="u2", conversation_id="k1") is None


async def test_forgetting_an_employee_deletes_their_cases_and_no_one_elses(pg) -> None:
    await _keep(901, "leaving", "k1")
    await _keep(901, "staying", "k2")

    async with tenant_scoped_session(901) as db:
        assert await forget_internal_cases(db, org_id=901, user_id="leaving") == 1
        await db.commit()

    assert await _visible_keys(901) == ["k2"]


async def test_the_nightly_prune_removes_old_cases_and_those_of_employees_no_longer_active(pg) -> None:
    """A store that finished after the employee was offboarded leaves a row
    forget_internal_cases never saw; the prune removes it with the expired ones."""
    await _keep(901, "u1", "young")
    await _keep(901, "gone", "after-offboarding")
    await _keep(902, "u2", "old")
    async with pg.begin() as conn:
        await conn.execute(
            text("UPDATE internal_chat_cases SET updated_at = now() - interval '31 days' WHERE conversation_id = 'old'")
        )
        await conn.execute(
            text(
                "INSERT INTO portal_users (org_id, zitadel_user_id, status) "
                "VALUES (901, 'u1', 'active'), (901, 'gone', 'offboarded'), (902, 'u2', 'active')"
            )
        )

    assert await prune_internal_cases() == 2

    assert await _visible_keys(901) == ["young"]
    assert await _visible_keys(902) == []
