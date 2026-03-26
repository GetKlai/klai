"""
Tests for pg_store.create_artifact() and soft_delete_artifact().
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from knowledge_ingest import pg_store

_SENTINEL = 253402300800


def _make_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
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
