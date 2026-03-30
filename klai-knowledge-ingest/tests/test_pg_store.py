"""
Tests for pg_store functions.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from knowledge_ingest import pg_store

_SENTINEL = 253402300800


def _make_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_create_artifact_returns_uuid():
    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        artifact_id = await pg_store.create_artifact(
            org_id="362757920133283846",
            kb_slug="personal",
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
    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        await pg_store.create_artifact(
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
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
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
    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        id1 = await pg_store.create_artifact("o", "kb", "p1.md", "observed", "factual", 0, None, 0, _SENTINEL)
        id2 = await pg_store.create_artifact("o", "kb", "p2.md", "observed", "factual", 0, None, 0, _SENTINEL)
    assert id1 != id2


@pytest.mark.asyncio
async def test_soft_delete_updates_belief_time_end():
    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        await pg_store.soft_delete_artifact("org123", "personal", "note.md")
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
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
    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        await pg_store.soft_delete_artifact("org", "kb", "path.md")
    sql = pool.execute.call_args[0][0]
    # Must filter on sentinel to avoid touching already-deleted records
    assert str(_SENTINEL) in sql or "$5" in sql


# -- list_personal_artifacts --------------------------------------------------


@pytest.mark.asyncio
async def test_list_personal_artifacts_queries_correct_params():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        result = await pg_store.list_personal_artifacts("org1", "user1", limit=10, offset=5)
    assert result == []
    pool.fetch.assert_called_once()
    call_args = pool.fetch.call_args[0]
    sql = call_args[0]
    assert "knowledge.artifacts" in sql
    assert "kb_slug = 'personal'" in sql
    values = call_args[1:]
    assert "org1" in values
    assert "user1" in values
    assert _SENTINEL in values
    assert 10 in values
    assert 5 in values


@pytest.mark.asyncio
async def test_list_personal_artifacts_returns_dicts():
    fake_row = MagicMock()
    fake_row.__iter__ = MagicMock(return_value=iter([("id", "abc"), ("path", "note.md")]))
    fake_row.items = MagicMock(return_value=[("id", "abc"), ("path", "note.md")])
    fake_row.keys = MagicMock(return_value=["id", "path"])
    fake_row.__getitem__ = lambda self, key: {"id": "abc", "path": "note.md"}[key]

    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[{"id": "abc", "path": "note.md", "assertion_mode": "fact", "created_at": 1700000000}])
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        result = await pg_store.list_personal_artifacts("org1", "user1")
    assert len(result) == 1
    assert result[0]["id"] == "abc"


# -- count_personal_artifacts -------------------------------------------------


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_int():
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=42)
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        count = await pg_store.count_personal_artifacts("org1", "user1")
    assert count == 42
    pool.fetchval.assert_called_once()
    sql = pool.fetchval.call_args[0][0]
    assert "COUNT(*)" in sql
    assert "kb_slug = 'personal'" in sql


@pytest.mark.asyncio
async def test_count_personal_artifacts_returns_zero_on_none():
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=None)
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        count = await pg_store.count_personal_artifacts("org1", "user1")
    assert count == 0


# -- get_personal_artifact ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_dict_when_found():
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={"id": "abc-123", "path": "note.md"})
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        result = await pg_store.get_personal_artifact("abc-123", "org1", "user1")
    assert result is not None
    assert result["id"] == "abc-123"
    assert result["path"] == "note.md"
    sql = pool.fetchrow.call_args[0][0]
    assert "kb_slug = 'personal'" in sql


@pytest.mark.asyncio
async def test_get_personal_artifact_returns_none_when_not_found():
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        result = await pg_store.get_personal_artifact("nonexistent", "org1", "user1")
    assert result is None


@pytest.mark.asyncio
async def test_update_artifact_extra_merges_jsonb(monkeypatch):
    """update_artifact_extra issues a JSONB merge UPDATE (AC-2)."""
    import json as json_mod

    pool = _make_pool()
    with patch("knowledge_ingest.pg_store.get_pool", new_callable=AsyncMock, return_value=pool):
        await pg_store.update_artifact_extra("art-001", {"graphiti_episode_id": "ep-xyz"})

    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    sql = call_args[0]
    assert "UPDATE knowledge.artifacts" in sql
    assert "COALESCE" in sql
    # First positional arg after SQL is the JSON patch
    patch_arg = call_args[1]
    patch_dict = json_mod.loads(patch_arg)
    assert patch_dict["graphiti_episode_id"] == "ep-xyz"
    # Second positional arg is the artifact_id
    assert call_args[2] == "art-001"
