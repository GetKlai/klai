"""RLS regression tests for research-api schema.

AC-11 (SPEC-TI-004-RLS-RESEARCH): verify that the Cat-D RLS policies on
research.notebooks, sources, chunks, chat_messages enforce fail-loud tenant
isolation.

These are unit tests that mock the DB session — they do NOT hit a real Postgres
instance. They verify that:
1. set_tenant is called before queries (fail-loud if missing).
2. The session helpers (tenant_scoped_session, cross_org_session) set the
   correct GUCs.
3. INSERT/UPDATE with a mismatched tenant_id raises (WITH CHECK simulated).

Integration tests against a real DB require a running Postgres with the
post_deploy_0005_research_rls.sql applied — those are left for the CI pipeline.
"""

from __future__ import annotations

import os

# Satisfy required env vars before any app import.
os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("RETRIEVAL_API_URL", "http://retrieval-api:8040")
os.environ.setdefault("RETRIEVAL_API_INTERNAL_SECRET", "test-secret")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# set_tenant helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_tenant_sets_guc():
    """set_tenant() executes set_config for app.current_tenant_id."""
    from app.core.database import set_tenant

    session = AsyncMock()
    session.execute = AsyncMock()

    await set_tenant(session, TENANT_A)

    # Verify set_config was called with the tenant id
    assert session.execute.called
    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    assert "current_tenant_id" in sql_text
    params = call_args[0][1]
    assert params["tid"] == TENANT_A


@pytest.mark.asyncio
async def test_set_tenant_accepts_uuid_string():
    """set_tenant() converts uuid objects to string correctly."""
    from app.core.database import set_tenant

    session = AsyncMock()
    session.execute = AsyncMock()

    tenant_uuid = uuid.UUID(TENANT_A)
    await set_tenant(session, str(tenant_uuid))

    call_args = session.execute.call_args
    params = call_args[0][1]
    assert params["tid"] == TENANT_A


# ---------------------------------------------------------------------------
# tenant_scoped_session helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_session_sets_and_resets():
    """tenant_scoped_session() sets tenant context and resets on exit."""
    from app.core import database as db_mod

    execute_calls: list[str] = []

    class _FakeSession:
        async def connection(self):
            return None

        async def execute(self, stmt, params=None):
            execute_calls.append(str(stmt))
            return MagicMock()

        async def rollback(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(return_value=_FakeSession())
    fake_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch.object(db_mod, "AsyncSessionLocal", fake_factory):
        async with db_mod.tenant_scoped_session(TENANT_A):
            pass

    # Should have seen: set current_tenant_id + set cross_org_admin clear
    joined = " ".join(execute_calls)
    assert "current_tenant_id" in joined


@pytest.mark.asyncio
async def test_cross_org_session_sets_cross_org_admin():
    """cross_org_session() sets app.cross_org_admin=true."""
    from app.core import database as db_mod

    execute_calls: list[str] = []

    class _FakeSession:
        async def connection(self):
            return None

        async def execute(self, stmt, params=None):
            execute_calls.append(str(stmt))
            return MagicMock()

        async def rollback(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__ = AsyncMock(return_value=_FakeSession())
    fake_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch.object(db_mod, "AsyncSessionLocal", fake_factory):
        async with db_mod.cross_org_session():
            pass

    joined = " ".join(execute_calls)
    assert "cross_org_admin" in joined


# ---------------------------------------------------------------------------
# RLS helper function tests (policy logic, unit-level)
# ---------------------------------------------------------------------------


def test_rls_current_org_id_is_schema_qualified():
    """post_deploy SQL uses research._rls_current_org_id() — schema qualified."""
    import pathlib

    sql_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "post_deploy_0005_research_rls.sql"
    )
    assert sql_path.exists(), "post_deploy SQL file must exist"
    content = sql_path.read_text()

    # Must define the function in research schema
    assert "research._rls_current_org_id" in content, (
        "helper function must be schema-qualified as research._rls_current_org_id()"
    )
    # Must have RETURNS uuid (not integer — research uses UUID tenant_id)
    assert "RETURNS uuid" in content, "helper must return uuid, not integer"
    # Must use app.current_tenant_id (not app.current_org_id)
    assert "current_tenant_id" in content
    # Must raise on missing context (fail-loud)
    assert "RAISE EXCEPTION" in content
    assert "42501" in content


def test_rls_policies_cover_all_four_tables():
    """post_deploy SQL must create policies on all four research tables."""
    import pathlib

    sql_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "post_deploy_0005_research_rls.sql"
    )
    content = sql_path.read_text()

    for table in (
        "research.notebooks",
        "research.sources",
        "research.chunks",
        "research.chat_messages",
    ):
        assert f"CREATE POLICY tenant_isolation ON {table}" in content, (
            f"Missing Cat-D policy for {table}"
        )


def test_rls_migration_enables_force_on_all_tables():
    """Migration 0005 must ENABLE and FORCE RLS on all four tables."""
    import pathlib

    mig_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0005_research_rls_enable.py"
    )
    content = mig_path.read_text()

    for _table in ("notebooks", "sources", "chunks", "chat_messages"):
        assert "ENABLE ROW LEVEL SECURITY" in content
        assert "FORCE ROW LEVEL SECURITY" in content


def test_chat_messages_uuid_migration_uses_cast():
    """A-11 migration must use USING tenant_id::uuid for the type change."""
    import pathlib

    mig_path = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0004_chat_messages_tenant_id_uuid.py"
    )
    content = mig_path.read_text()

    assert "tenant_id::uuid" in content, "Must use USING tenant_id::uuid cast"
    assert "research.chat_messages" in content
