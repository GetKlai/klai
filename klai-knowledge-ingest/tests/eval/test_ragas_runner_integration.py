"""
Integration tests for knowledge_ingest.eval.ragas_runner (Unit 3).

All external HTTP calls and RAGAS are mocked. The DB pool is also mocked.
Tests exercise the full per-query loop in evaluate_retrieval_quality_nightly.

Coverage:
  - Full run: 3 queries -> 3 rows written with non-null metrics.
  - Retrieval failure: row inserted with NULL metrics + meta.error (REQ-3).
  - Judge failure: row inserted with available metrics, failed metric NULL.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(row_id: int = 1):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=row_id)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value=None)
    return pool


def _make_retrieval_result(chunks=None):
    from knowledge_ingest.eval.retrieval_client import RetrievalResult

    return RetrievalResult(
        chunks=chunks
        or [{"id": "c1", "title": "Bubble troubleshoot", "text": "Context text for eval"}],
        retrieval_ms=150,
        total_tokens=200,
    )


def _make_retrieval_failure(reason="HTTP 500: Internal Server Error"):
    from knowledge_ingest.eval.retrieval_client import RetrievalFailure

    return RetrievalFailure(reason=reason)


_CANNED_METRICS = {
    "context_precision": 0.85,
    "context_recall": 0.90,
    "faithfulness": 0.88,
    "answer_relevance": 0.92,
}


def _make_app():
    from procrastinate import App
    from procrastinate.testing import InMemoryConnector

    connector = InMemoryConnector()
    app = App(connector=connector)
    app.open()
    return app, connector


def _sample_suite_path():
    from pathlib import Path

    return Path(__file__).parents[2] / "knowledge_ingest" / "eval" / "suites" / "_sample.yaml"


@pytest.mark.asyncio
async def test_full_run_against_sample_suite() -> None:
    """3 queries in _sample.yaml -> 3 rows written, all metrics non-null."""
    os.environ.pop("RAG_EVAL_VARIANT", None)
    pool = _make_pool()
    app, _connector = _make_app()
    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    register_eval_tasks(app)
    task_fn = app.evaluate_retrieval_quality_nightly

    with (
        patch("knowledge_ingest.eval.store.get_pool", new_callable=AsyncMock, return_value=pool),
        patch(
            "knowledge_ingest.eval.retrieval_client.retrieve_chunks",
            new_callable=AsyncMock,
            return_value=_make_retrieval_result(),
        ),
        patch(
            "knowledge_ingest.eval.judge_client.generate_answer",
            new_callable=AsyncMock,
            return_value="Mocked model answer for the query.",
        ),
        patch(
            "knowledge_ingest.eval.judge_client.evaluate_query",
            new_callable=AsyncMock,
            return_value=_CANNED_METRICS.copy(),
        ),
        patch(
            "knowledge_ingest.eval.ragas_runner.settings.rag_eval_suites_dir",
            str(_sample_suite_path().parent),
        ),
    ):
        result = await task_fn(suite="_sample")

    assert result["queries_processed"] == 3
    assert result["rows_written"] == 3
    assert pool.fetchval.call_count == 3


@pytest.mark.asyncio
async def test_run_with_retrieval_failure() -> None:
    """REQ-3: retrieval failure for query[0] -> row with NULL metrics + meta.error."""
    os.environ.pop("RAG_EVAL_VARIANT", None)
    pool = _make_pool()
    app, _connector = _make_app()
    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    register_eval_tasks(app)
    task_fn = app.evaluate_retrieval_quality_nightly

    retrieve_side_effects = [
        _make_retrieval_failure("HTTP 500: Internal Server Error"),
        _make_retrieval_result(),
        _make_retrieval_result(),
    ]

    with (
        patch("knowledge_ingest.eval.store.get_pool", new_callable=AsyncMock, return_value=pool),
        patch(
            "knowledge_ingest.eval.retrieval_client.retrieve_chunks",
            new_callable=AsyncMock,
            side_effect=retrieve_side_effects,
        ),
        patch(
            "knowledge_ingest.eval.judge_client.generate_answer",
            new_callable=AsyncMock,
            return_value="Answer for successful queries",
        ),
        patch(
            "knowledge_ingest.eval.judge_client.evaluate_query",
            new_callable=AsyncMock,
            return_value=_CANNED_METRICS.copy(),
        ),
        patch(
            "knowledge_ingest.eval.ragas_runner.settings.rag_eval_suites_dir",
            str(_sample_suite_path().parent),
        ),
    ):
        result = await task_fn(suite="_sample")

    assert result["queries_processed"] == 3
    assert result["rows_written"] == 3

    first_call_kwargs = pool.fetchval.call_args_list[0]
    params = list(first_call_kwargs[0][1:])
    none_count = sum(1 for p in params if p is None)
    assert none_count >= 4, f"Expected >= 4 None params in failure row, got {none_count}"

    meta_params = [p for p in params if isinstance(p, str) and "retrieval_failed" in p]
    assert meta_params, "Expected meta.error=retrieval_failed in first INSERT params"


@pytest.mark.asyncio
async def test_run_with_judge_failure() -> None:
    """Judge failure: available metrics land, failed metric is NULL."""
    os.environ.pop("RAG_EVAL_VARIANT", None)
    pool = _make_pool()
    app, _connector = _make_app()
    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    register_eval_tasks(app)
    task_fn = app.evaluate_retrieval_quality_nightly

    partial_metrics = {
        "context_precision": 0.75,
        "context_recall": 0.80,
        "faithfulness": None,
        "answer_relevance": 0.70,
    }

    with (
        patch("knowledge_ingest.eval.store.get_pool", new_callable=AsyncMock, return_value=pool),
        patch(
            "knowledge_ingest.eval.retrieval_client.retrieve_chunks",
            new_callable=AsyncMock,
            return_value=_make_retrieval_result(),
        ),
        patch(
            "knowledge_ingest.eval.judge_client.generate_answer",
            new_callable=AsyncMock,
            return_value="Test answer",
        ),
        patch(
            "knowledge_ingest.eval.judge_client.evaluate_query",
            new_callable=AsyncMock,
            return_value=partial_metrics.copy(),
        ),
        patch(
            "knowledge_ingest.eval.ragas_runner.settings.rag_eval_suites_dir",
            str(_sample_suite_path().parent),
        ),
    ):
        result = await task_fn(suite="_sample")

    assert result["rows_written"] == 3
    for i, call_args in enumerate(pool.fetchval.call_args_list):
        params = list(call_args[0][1:])
        assert None in params, f"Expected faithfulness=None in row {i}"
        float_params = [p for p in params if isinstance(p, float)]
        assert float_params, f"Expected some float metrics in row {i}"
