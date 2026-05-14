"""
Tests for pg_store functions.

SPEC-TI-003-FOLLOWUP-001: pg_store helpers no longer acquire pool
connections themselves -- callers pass the GUC-pinned ``asyncpg.Connection``
in. These unit tests therefore construct a mock conn and assert against
``conn.execute`` / ``conn.fetch`` / ``conn.fetchval`` / ``conn.fetchrow``
directly.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import pg_store

_SENTINEL = 253402300800


def _make_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _tx():
        yield None

    conn.transaction = MagicMock(side_effect=_tx)
    return conn


@pytest.mark.asyncio
async def test_create_artifact_returns_uuid():
    conn = _make_conn()
    artifact_id = await pg_store.create_artifact(
        conn,
        org_id="100000000000000001",
        kb_slug="personal-user456",
        path="note.md",
        provenance_type="observed",
        assertion_mode="factual",
        synthesis_depth=0,
        confidence=None,
        belief_time_start=1705276800,
        belief_time_end=_SENTINEL,
    )
    assert isinstance(artifact_id, str)
    assert len(artifact_id) == 36  # UUID format
    assert artifact_id.count("-") == 4


@pytest.mark.asyncio
async def test_create_artifact_executes_insert():
    conn = _make_conn()
    await pg_store.create_artifact(
        conn,
        org_id="org123",
        kb_slug="docs",
        path="spec.md",
        provenance_type="synthesized",
        assertion_mode="belief",
        synthesis_depth=3,
        confidence="high",
        belief_time_start=1705276800,
        belief_time_end=_SENTINEL,
        user_id="user456",
    )
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    # Verify all values are passed in correct order
    assert "INSERT INTO knowledge.artifacts" in call_args[0]
    values = call_args[1:]
    assert "org123" in values
    assert "docs" in values
    assert "spec.md" in values
    assert "synthesized" in values
    assert "belief" in values
    assert 3 in values
    assert "high" in values
    assert "user456" in values


@pytest.mark.asyncio
async def test_create_artifact_generates_unique_ids():
    conn = _make_conn()
    id1 = await pg_store.create_artifact(
        conn, "o", "kb", "p1.md", "observed", "factual", 0, None, 0, _SENTINEL
    )
    id2 = await pg_store.create_artifact(
        conn, "o", "kb", "p2.md", "observed", "factual", 0, None, 0, _SENTINEL
    )
    assert id1 != id2


@pytest.mark.asyncio
async def test_soft_delete_updates_belief_time_end():
    conn = _make_conn()
    await pg_store.soft_delete_artifact(conn, "org123", "personal", "note.md")
    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    assert "UPDATE knowledge.artifacts" in call_args[0]
    assert "belief_time_end" in call_args[0]
    values = call_args[1:]
    assert "org123" in values
    assert "personal" in values
    assert "note.md" in values
    assert _SENTINEL in values


@pytest.mark.asyncio
async def test_soft_delete_only_updates_active_records():
    """Verifies the WHERE belief_time_end = SENTINEL constraint is present."""
    conn = _make_conn()
    await pg_store.soft_delete_artifact(conn, "org", "kb", "path.md")
    sql = conn.execute.call_args[0][0]
    # Must filter on sentinel to avoid touching already-deleted records
    assert str(_SENTINEL) in sql or "$5" in sql


@pytest.mark.asyncio
async def test_list_stale_connector_artifact_paths_excludes_current_paths():
    """Connector reconciliation only returns active paths absent from latest crawl."""
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"path": "https://getklai.com/"},
            {"path": "https://getklai.com/contact"},
        ]
    )

    result = await pg_store.list_stale_connector_artifact_paths(
        conn,
        org_id="org",
        kb_slug="kb",
        connector_id="conn-1",
        current_paths=["https://www.getklai.com/"],
    )

    assert result == ["https://getklai.com/", "https://getklai.com/contact"]
    sql = conn.fetch.call_args[0][0]
    assert "source_connector_id" in sql
    assert "NOT (path = ANY($5::text[]))" in sql
    assert conn.fetch.call_args[0][5] == ["https://www.getklai.com/"]


@pytest.mark.asyncio
async def test_soft_delete_stale_connector_artifacts_scrubs_registry_and_links():
    """Stale connector cleanup retires artifacts and removes crawl metadata."""
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=2)

    deleted = await pg_store.soft_delete_stale_connector_artifacts(
        conn,
        org_id="org",
        kb_slug="kb",
        connector_id="conn-1",
        stale_paths=["https://getklai.com/", "https://getklai.com/contact"],
    )

    assert deleted == 2
    executed_sql = "\n".join(call.args[0] for call in conn.execute.await_args_list)
    final_sql = conn.fetchval.call_args[0][0]
    assert "DELETE FROM knowledge.crawled_pages" in executed_sql
    assert "DELETE FROM knowledge.page_links" in executed_sql
    assert "source_connector_id" in final_sql
    assert "belief_time_end = $6" in final_sql


# -- list_personal_artifacts --------------------------------------------------


@pytest.mark.asyncio
async def test_list_personal_artifacts_queries_correct_params():
    conn = _make_conn()
    conn.fetch = AsyncMock(return_value=[])
    result = await pg_store.list_personal_artifacts(conn, "org1", "user1", limit=10, offset=5)
    assert result == []
    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    sql = call_args[0]
    assert "knowledge.artifacts" in sql
    assert "kb_slug = $3" in sql
    values = call_args[1:]
    assert "org1" in values
    assert "user1" in values
    assert "personal-user1" in values
    assert _SENTINEL in values
    assert 10 in values
    assert 5 in values


@pytest.mark.asyncio
async def test_list_personal_artifacts_returns_dicts():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "abc",
                "path": "note.md",
                "assertion_mode": "fact",
                "created_at": 1700000000,
            }
        ]
    )
    result = await pg_store.list_personal_artifacts(conn, "org1", "user1")
    assert len(result) == 1
    assert result[0]["id"] == "abc"


# -- count_personal_artifacts -------------------------------------------------


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_int():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=42)
    count = await pg_store.count_personal_artifacts(conn, "org1", "user1")
    assert count == 42
    conn.fetchval.assert_called_once()
    sql = conn.fetchval.call_args[0][0]
    assert "COUNT(*)" in sql
    assert "kb_slug = $3" in sql


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_zero_on_none():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=None)
    count = await pg_store.count_personal_artifacts(conn, "org1", "user1")
    assert count == 0


# -- get_personal_artifact ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_dict_when_found():
    conn = _make_conn()
    conn.fetchrow = AsyncMock(return_value={"id": "abc-123", "path": "note.md"})
    result = await pg_store.get_personal_artifact(conn, "abc-123", "org1", "user1")
    assert result is not None
    assert result["id"] == "abc-123"
    assert result["path"] == "note.md"
    sql = conn.fetchrow.call_args[0][0]
    assert "kb_slug = $4" in sql


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_none_when_not_found():
    conn = _make_conn()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await pg_store.get_personal_artifact(conn, "nonexistent", "org1", "user1")
    assert result is None


@pytest.mark.asyncio
async def test_update_artifact_extra_merges_jsonb():
    """update_artifact_extra issues a JSONB merge UPDATE (AC-2)."""
    import json as json_mod

    conn = _make_conn()
    await pg_store.update_artifact_extra(conn, "art-001", {"graphiti_episode_id": "ep-xyz"})

    conn.execute.assert_called_once()
    call_args = conn.execute.call_args[0]
    sql = call_args[0]
    assert "UPDATE knowledge.artifacts" in sql
    assert "COALESCE" in sql
    # First positional arg after SQL is the JSON patch
    patch_arg = call_args[1]
    patch_dict = json_mod.loads(patch_arg)
    assert patch_dict["graphiti_episode_id"] == "ep-xyz"
    # Second positional arg is the artifact_id
    assert call_args[2] == "art-001"
