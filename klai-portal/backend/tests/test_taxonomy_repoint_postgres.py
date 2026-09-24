"""Real-PostgreSQL proof that deleting or merging a taxonomy node never leaves
its id behind on gap rows (portal_retrieval_gaps.taxonomy_node_ids).

The array rewrite (replace without duplicates, drop to NULL when empty) and the
Cat-D tenant scoping are database behaviour, so this runs the real endpoint
functions against a throwaway schema with the production RLS policy shape and a
non-superuser role, like ``test_support_cases_postgres.py``. The Qdrant side is
mocked at the knowledge-ingest client; its contract is covered in
klai-knowledge-ingest.

Run: ``RLS_TEST_DATABASE_URL=... uv run pytest tests/test_taxonomy_repoint_postgres.py -m postgres -q``
"""

from __future__ import annotations

import os
import types
from collections.abc import AsyncIterator, Iterator
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.api import taxonomy
from app.core.database import TenantContextSession, set_tenant
from app.core.permissions import UserPermissions

pytestmark = pytest.mark.postgres

_SCHEMA = "taxonomy_repoint_test_schema"
_ROLE = "taxonomy_repoint_test_role"
_PW = "taxonomy_repoint_test_pw"  # throwaway role in a disposable schema
_ORG = 901
_FOREIGN_ORG = 902
_KB_ID = 1
# Only org_id and user_id are read on these paths.
_PERMS = cast(UserPermissions, types.SimpleNamespace(org_id=_ORG, user_id="u"))

_SETUP = [
    f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE",
    # S608: every interpolated value is a module constant, never input.
    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}') "  # noqa: S608
    f"THEN CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PW}'; END IF; END $$;",
    f"CREATE SCHEMA {_SCHEMA}",
    f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {_ROLE}",
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
    f"""CREATE TABLE {_SCHEMA}.portal_taxonomy_nodes (
        id serial PRIMARY KEY, kb_id integer NOT NULL,
        parent_id integer REFERENCES {_SCHEMA}.portal_taxonomy_nodes(id) ON DELETE SET NULL,
        name varchar(128) NOT NULL, slug varchar(128) NOT NULL, sort_order integer NOT NULL DEFAULT 0,
        description text, created_at timestamptz DEFAULT now(), created_by varchar(64) NOT NULL)""",
    f"""CREATE TABLE {_SCHEMA}.portal_taxonomy_proposals (
        id serial PRIMARY KEY, kb_id integer NOT NULL, proposal_type varchar(32) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'pending', title varchar(256) NOT NULL, payload jsonb NOT NULL,
        confidence_score double precision, created_at timestamptz DEFAULT now(), reviewed_at timestamptz,
        reviewed_by varchar(64), rejection_reason text)""",
    f"""CREATE TABLE {_SCHEMA}.portal_retrieval_gaps (
        id serial PRIMARY KEY, org_id integer NOT NULL, query_text text NOT NULL, taxonomy_node_ids integer[])""",
    # Cat-D strict RLS on the gap table (the production policy shape).
    f"ALTER TABLE {_SCHEMA}.portal_retrieval_gaps ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {_SCHEMA}.portal_retrieval_gaps FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_isolation ON {_SCHEMA}.portal_retrieval_gaps "
    f"USING ({_SCHEMA}._rls_current_org_id() IS NULL OR org_id = {_SCHEMA}._rls_current_org_id())",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {_SCHEMA} TO {_ROLE}",
    f"GRANT EXECUTE ON FUNCTION {_SCHEMA}._rls_current_org_id() TO {_ROLE}",
]

# Node ids are fixed so gap arrays can be written literally: 1=Billing (source),
# 2=Invoices (target), 3=Login, 4=Refunds (child of Billing).
_SEED = [
    "INSERT INTO portal_taxonomy_nodes (id, kb_id, parent_id, name, slug, created_by) VALUES "
    "(1, 1, NULL, 'Billing', 'billing', 'u'), (2, 1, NULL, 'Invoices', 'invoices', 'u'), "
    "(3, 1, NULL, 'Login', 'login', 'u'), (4, 1, 1, 'Refunds', 'refunds', 'u')",
    "SELECT setval('portal_taxonomy_nodes_id_seq', 10)",
]

_SRC, _TARGET, _OTHER, _CHILD = 1, 2, 3, 4


def _role_dsn(dsn: str) -> str:
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{_ROLE}:{_PW}@{hostpart}"


@pytest.fixture
async def pg() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker]]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set")
    search_path = {"server_settings": {"search_path": _SCHEMA}}
    admin = create_async_engine(dsn, connect_args=search_path)
    async with admin.begin() as conn:
        for stmt in _SETUP + _SEED:
            await conn.execute(text(stmt))
    role_engine = create_async_engine(_role_dsn(dsn), connect_args=search_path)
    factory = async_sessionmaker(role_engine, class_=TenantContextSession, expire_on_commit=False)
    try:
        yield admin, factory
    finally:
        await role_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await admin.dispose()


@pytest.fixture
def chunks() -> Iterator[AsyncMock]:
    """Stub the request-scoped lookups the real DB schema here does not carry,
    and the knowledge-ingest chunk update."""
    kb = types.SimpleNamespace(id=_KB_ID, org_id=_ORG, slug="kb-a")
    org = types.SimpleNamespace(zitadel_org_id="zit-901")
    mock = AsyncMock(return_value={"chunks_updated": 3})
    with (
        patch.object(taxonomy, "_get_kb_or_404", AsyncMock(return_value=kb)),
        patch.object(taxonomy, "_require_role", AsyncMock(return_value="owner")),
        patch.object(taxonomy, "_load_org_or_500", AsyncMock(return_value=org)),
        patch("app.services.knowledge_ingest_client.remove_taxonomy_node_from_chunks", mock),
    ):
        yield mock


async def _seed_gaps(admin: AsyncEngine, rows: list[tuple[int, str, list[int]]]) -> None:
    async with admin.begin() as conn:
        for org_id, query, node_ids in rows:
            await conn.execute(
                text("INSERT INTO portal_retrieval_gaps (org_id, query_text, taxonomy_node_ids) VALUES (:o, :q, :n)"),
                {"o": org_id, "q": query, "n": node_ids},
            )


async def _gap_topics(admin: AsyncEngine) -> dict[str, list[int] | None]:
    async with admin.connect() as conn:
        rows = (await conn.execute(text("SELECT query_text, taxonomy_node_ids FROM portal_retrieval_gaps"))).all()
    return {row[0]: row[1] for row in rows}


async def _node_ids(admin: AsyncEngine) -> set[int]:
    async with admin.connect() as conn:
        return set((await conn.execute(text("SELECT id FROM portal_taxonomy_nodes"))).scalars().all())


async def _delete(factory, node_id: int, reassign_to_node_id: int | None) -> dict:
    async with factory() as db:
        await set_tenant(db, _ORG)
        return await taxonomy.delete_taxonomy_node(
            kb_slug="kb-a",
            node_id=node_id,
            reassign_to_node_id=reassign_to_node_id,
            perms=_PERMS,
            db=db,
        )


_GAPS = [
    (_ORG, "only-source", [_SRC]),
    (_ORG, "source-and-target", [_SRC, _TARGET]),
    (_ORG, "source-and-other", [_OTHER, _SRC]),
    (_ORG, "untouched", [_OTHER]),
    # Same id on another tenant's row: must survive (explicit org predicate + RLS).
    (_FOREIGN_ORG, "foreign", [_SRC]),
]


async def test_delete_with_reassign_moves_gap_topic_to_target(pg, chunks) -> None:
    admin, factory = pg
    await _seed_gaps(admin, _GAPS)

    await _delete(factory, _SRC, reassign_to_node_id=_TARGET)

    assert await _gap_topics(admin) == {
        "only-source": [_TARGET],
        "source-and-target": [_TARGET],
        "source-and-other": [_OTHER, _TARGET],
        "untouched": [_OTHER],
        "foreign": [_SRC],
    }
    chunks.assert_awaited_once_with("zit-901", "kb-a", _SRC, replacement_node_id=_TARGET)
    assert _SRC not in await _node_ids(admin)


async def test_delete_without_reassign_drops_gap_topic(pg, chunks) -> None:
    admin, factory = pg
    await _seed_gaps(admin, _GAPS)

    await _delete(factory, _SRC, reassign_to_node_id=None)

    assert await _gap_topics(admin) == {
        "only-source": None,
        "source-and-target": [_TARGET],
        "source-and-other": [_OTHER],
        "untouched": [_OTHER],
        "foreign": [_SRC],
    }
    chunks.assert_awaited_once_with("zit-901", "kb-a", _SRC, replacement_node_id=None)


async def test_delete_rejects_reassign_to_own_descendant(pg, chunks) -> None:
    admin, factory = pg
    await _seed_gaps(admin, _GAPS)

    with pytest.raises(HTTPException) as exc:
        await _delete(factory, _SRC, reassign_to_node_id=_CHILD)

    assert exc.value.status_code == 409
    chunks.assert_not_awaited()
    assert _SRC in await _node_ids(admin)


async def test_failed_chunk_update_keeps_node_and_gap_rows(pg, chunks) -> None:
    admin, factory = pg
    await _seed_gaps(admin, _GAPS)
    before = await _gap_topics(admin)
    chunks.side_effect = RuntimeError("ingest down")

    with pytest.raises(HTTPException) as exc:
        await _delete(factory, _SRC, reassign_to_node_id=_TARGET)

    assert exc.value.status_code == 502
    assert await _gap_topics(admin) == before
    assert await _node_ids(admin) == {_SRC, _TARGET, _OTHER, _CHILD}


async def test_merge_proposal_moves_source_gaps_to_target_without_duplicates(pg, chunks) -> None:
    admin, factory = pg
    await _seed_gaps(admin, _GAPS)
    async with admin.begin() as conn:
        proposal_id = (
            await conn.execute(
                text(
                    "INSERT INTO portal_taxonomy_proposals (kb_id, proposal_type, title, payload) "
                    "VALUES (1, 'merge', 'Billing into Invoices', CAST(:p AS jsonb)) RETURNING id"
                ),
                {"p": f'{{"source_node_id": {_SRC}, "target_node_id": {_TARGET}}}'},
            )
        ).scalar_one()

    async with factory() as db:
        await set_tenant(db, _ORG)
        await taxonomy.approve_proposal(
            kb_slug="kb-a",
            proposal_id=proposal_id,
            body=None,
            auto_categorise=False,
            perms=_PERMS,
            db=db,
        )

    assert await _gap_topics(admin) == {
        "only-source": [_TARGET],
        "source-and-target": [_TARGET],
        "source-and-other": [_OTHER, _TARGET],
        "untouched": [_OTHER],
        "foreign": [_SRC],
    }
    chunks.assert_awaited_once_with("zit-901", "kb-a", _SRC, replacement_node_id=_TARGET)
    assert _SRC not in await _node_ids(admin)
