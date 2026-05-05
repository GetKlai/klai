"""
Tests for knowledge_ingest.eval.judge_client.

RED phase: tests fail until judge_client.py exists.

Coverage:
  - generate_answer: 200 response returns a non-empty string.
  - generate_answer: 500 response returns None (no raise).
  - evaluate_query: mocked ragas.evaluate returns dict with all 4 metric keys.
  - evaluate_query: single metric failure returns None for that metric, others survive.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    from unittest.mock import MagicMock

    s = MagicMock()
    s.litellm_url = overrides.get("litellm_url", "http://litellm:4000")
    s.litellm_api_key = overrides.get("litellm_api_key", "test-key")
    s.rag_eval_judge_model = overrides.get("rag_eval_judge_model", "klai-fast")
    s.rag_eval_faithfulness_model = overrides.get("rag_eval_faithfulness_model", "klai-medium")
    s.rag_eval_embeddings_model = overrides.get("rag_eval_embeddings_model", "klai-bge-m3")
    s.rag_eval_judge_timeout = overrides.get("rag_eval_judge_timeout", 30)
    return s


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int, json_body: dict | None = None):
        self._status_code = status_code
        self._json_body = json_body or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        content = json.dumps(self._json_body).encode()
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=content,
            request=request,
        )


_CHAT_200_BODY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Bubble is een browser plugin die soms herstart moet worden.",
            }
        }
    ]
}


# ---------------------------------------------------------------------------
# Test 1 — generate_answer success returns string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_answer_returns_string() -> None:
    """200 chat completion: generate_answer returns a non-empty string."""
    transport = _MockTransport(status_code=200, json_body=_CHAT_200_BODY)

    from knowledge_ingest.eval.judge_client import generate_answer

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Bubble is een browser plugin."}]

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await generate_answer(
            query="Hoe troubleshoot ik Bubble?",
            chunks=chunks,
            _transport=transport,
        )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Bubble" in result


# ---------------------------------------------------------------------------
# Test 2 — generate_answer failure returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_answer_failure_returns_none() -> None:
    """HTTP 500 from judge: generate_answer returns None (no raise)."""
    transport = _MockTransport(
        status_code=500,
        json_body={"detail": "Internal server error"},
    )

    from knowledge_ingest.eval.judge_client import generate_answer

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Some context"}]

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await generate_answer(
            query="Test query",
            chunks=chunks,
            _transport=transport,
        )

    assert result is None


# ---------------------------------------------------------------------------
# Test 3 — evaluate_query returns metrics dict with all 4 keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_returns_metrics_dict() -> None:
    """evaluate_query returns a dict with all 4 metric keys when RAGAS succeeds."""

    # Mock the RAGAS evaluate call at the module level where it is used
    canned_scores = [
        {
            "context_precision": 0.85,
            "context_recall": 0.90,
            "faithfulness": 0.88,
            "answer_relevancy": 0.92,
        }
    ]
    mock_result = MagicMock()
    mock_result.scores = canned_scores

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Context text"}]

    with (
        patch("knowledge_ingest.eval.judge_client.settings", settings),
        patch(
            "knowledge_ingest.eval.judge_client._run_ragas_evaluate",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
    ):
        result = await evaluate_query(
            query="Hoe troubleshoot ik Bubble?",
            chunks=chunks,
            answer="Bubble is een plugin.",
            expected_topics=["bubble", "browser-plugin"],
        )

    assert "context_precision" in result
    assert "context_recall" in result
    assert "faithfulness" in result
    assert "answer_relevance" in result
    assert result["context_precision"] == pytest.approx(0.85)
    assert result["faithfulness"] == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# Test 4 — partial metric failure: failed metric is None, others survive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_partial_failure() -> None:
    """When RAGAS raises for one metric, that metric is None; others are preserved."""

    # Simulate RAGAS returning scores where faithfulness is missing (None-valued)
    canned_scores = [
        {
            "context_precision": 0.75,
            "context_recall": 0.80,
            # faithfulness absent — simulates per-metric failure
            "answer_relevancy": 0.70,
        }
    ]
    mock_result = MagicMock()
    mock_result.scores = canned_scores

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Context text"}]

    with (
        patch("knowledge_ingest.eval.judge_client.settings", settings),
        patch(
            "knowledge_ingest.eval.judge_client._run_ragas_evaluate",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
    ):
        result = await evaluate_query(
            query="Test query",
            chunks=chunks,
            answer="Test answer",
            expected_topics=["test"],
        )

    # faithfulness missing from scores => None
    assert result["faithfulness"] is None
    # Others present
    assert result["context_precision"] == pytest.approx(0.75)
    assert result["context_recall"] == pytest.approx(0.80)
    assert result["answer_relevance"] == pytest.approx(0.70)
