"""
Tests for knowledge_ingest.eval.store.insert_eval_row.

RED phase: all tests fail until the migration and store module exist.

Coverage:
  - REQ-2: storage shape (round-trip)
  - REQ-3: NULL metrics on failure-mode rows
  - REQ-6: default variant = 'baseline'
  - Schema: both indexes exist in pg_indexes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(fetchval_return=None):
    """Return a minimal asyncpg pool mock."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_insert_eval_row_round_trip():
    """insert_eval_row issues a parameterised INSERT and returns the new id."""
    pool = _make_pool(fetchval_return=42)

    with patch(
        "knowledge_ingest.eval.store.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        from knowledge_ingest.eval.store import insert_eval_row

        result_id = await insert_eval_row(
            suite="chat",
            variant="baseline",
            query_id="chat-faq-1",
            context_precision=0.85,
            context_recall=0.90,
            faithfulness=0.88,
            answer_relevance=0.92,
            retrieved_chunk_ids=["chunk-a", "chunk-b"],
            retrieval_ms=320,
            total_tokens=1500,
            meta={"kb_artifact_count": 501},
        )

    assert result_id == 42
    pool.fetchval.assert_called_once()
    sql, *params = pool.fetchval.call_args[0]
    assert "INSERT INTO knowledge.rag_eval_results" in sql
    assert "RETURNING id" in sql
    # Parameterised — no f-string values in SQL
    assert "chat" not in sql
    assert "chat-faq-1" not in sql
    # Values passed as positional args
    assert "chat" in params
    assert "chat-faq-1" in params
    assert 0.85 in params
    assert ["chunk-a", "chunk-b"] in params


@pytest.mark.asyncio
async def test_insert_eval_row_handles_none_metrics():
    """REQ-3: failure-mode rows must persist with NULL metrics (all four as None)."""
    pool = _make_pool(fetchval_return=7)

    with patch(
        "knowledge_ingest.eval.store.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        from knowledge_ingest.eval.store import insert_eval_row

        result_id = await insert_eval_row(
            suite="chat",
            variant="baseline",
            query_id="chat-fail-1",
            context_precision=None,
            context_recall=None,
            faithfulness=None,
            answer_relevance=None,
            retrieved_chunk_ids=[],
            retrieval_ms=None,
            total_tokens=None,
            meta={"error": "retrieval_failed: HTTP 500"},
        )

    assert result_id == 7
    pool.fetchval.assert_called_once()
    _sql, *params = pool.fetchval.call_args[0]
    # All four metric params are None
    none_count = sum(1 for p in params if p is None)
    assert none_count >= 4


@pytest.mark.asyncio
async def test_insert_eval_row_default_variant():
    """REQ-6: omitting variant uses 'baseline' as the default."""
    pool = _make_pool(fetchval_return=99)

    with patch(
        "knowledge_ingest.eval.store.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        from knowledge_ingest.eval.store import insert_eval_row

        await insert_eval_row(
            suite="knowledge_org",
            # variant omitted — should default to 'baseline'
            query_id="org-q-1",
            context_precision=0.7,
            context_recall=0.8,
            faithfulness=0.9,
            answer_relevance=0.75,
            retrieved_chunk_ids=[],
            retrieval_ms=200,
            total_tokens=800,
            meta={},
        )

    _sql, *params = pool.fetchval.call_args[0]
    assert "baseline" in params


@pytest.mark.asyncio
async def test_table_indexes_exist():
    """Both ix_rag_eval_run_at_suite and ix_rag_eval_variant_run_at must exist.

    Verifies the migration DDL query pattern by asserting the store module
    queries pg_indexes correctly. Uses a mock pool returning both index names.
    """
    expected_indexes = {"ix_rag_eval_run_at_suite", "ix_rag_eval_variant_run_at"}

    # Simulate pg_indexes returning both rows
    fake_rows = [
        {"indexname": "ix_rag_eval_run_at_suite"},
        {"indexname": "ix_rag_eval_variant_run_at"},
    ]
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=fake_rows)

    with patch(
        "knowledge_ingest.eval.store.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        from knowledge_ingest.eval.store import get_eval_index_names

        found = await get_eval_index_names()

    assert expected_indexes == found
    pool.fetch.assert_called_once()
    sql = pool.fetch.call_args[0][0]
    assert "pg_indexes" in sql
    assert "rag_eval_results" in sql
