"""Tests for ``knowledge_ingest.eval.judge_client``.

Covers the RAGAS 0.4.3 ``ragas.metrics.collections`` per-metric ``ascore()``
API used by the evaluation harness:

  - ``generate_answer``: 200 → string, non-200 → None.
  - ``evaluate_query``: each metric class is constructed and its ``ascore``
    coroutine is awaited; success path returns floats, per-metric failure
    returns None for that metric while the others survive.
  - ``_safe_ascore``: never raises; converts None / missing ``.value`` to None.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
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


class _CannedMetric:
    """Stand-in for a ``ragas.metrics.collections`` metric class.

    Records all ``ascore(...)`` calls and returns either a canned
    ``MetricResult``-shaped object (with ``.value``) or raises a configured
    exception, per ``evaluate_query``'s parallel-gather contract.
    """

    def __init__(self, value: float | None, *, raises: Exception | None = None):
        self.value = value
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        # The metric class itself is used as a constructor; tests don't care
        # about constructor kwargs (llm/embeddings) — they just want the same
        # instance back so they can inspect ``calls``.
        return self

    async def ascore(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(value=self.value)


@pytest.fixture
def _patch_module_deps(monkeypatch):
    """Patch the heavy RAGAS dependencies to no-op stubs.

    ``evaluate_query`` builds a RAGAS LLM + embeddings up-front; for tests
    we don't want to construct real OpenAI clients. The metric classes
    themselves are patched per-test by injecting a fake
    ``ragas.metrics.collections`` module so we can assert ``ascore`` was
    awaited correctly.
    """
    monkeypatch.setattr(
        "knowledge_ingest.eval.judge_client._build_ragas_llm",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "knowledge_ingest.eval.judge_client._build_ragas_embeddings",
        MagicMock(return_value=MagicMock()),
    )
    yield


def _install_fake_collections(monkeypatch, **metric_overrides):
    """Replace ``ragas.metrics.collections`` with a stub exposing the 4 metrics.

    Pass ``ContextPrecision=_CannedMetric(0.85)`` etc to control the result
    of each metric's ``ascore`` call.
    """
    import sys
    import types

    defaults = {
        "ContextPrecision": _CannedMetric(0.85),
        "ContextRecall": _CannedMetric(0.90),
        "Faithfulness": _CannedMetric(0.88),
        "AnswerRelevancy": _CannedMetric(0.92),
    }
    defaults.update(metric_overrides)

    fake = types.ModuleType("ragas.metrics.collections")
    for name, metric in defaults.items():
        setattr(fake, name, metric)

    monkeypatch.setitem(sys.modules, "ragas.metrics.collections", fake)
    return defaults


# ---------------------------------------------------------------------------
# generate_answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_answer_returns_string() -> None:
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
    assert "Bubble" in result


@pytest.mark.asyncio
async def test_generate_answer_failure_returns_none() -> None:
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
# evaluate_query — happy path (4 metrics in parallel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_returns_metrics_dict(monkeypatch, _patch_module_deps) -> None:
    """All four metrics succeed → dict has all 4 floats."""
    metrics = _install_fake_collections(monkeypatch)

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Context text"}]

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await evaluate_query(
            query="Hoe troubleshoot ik Bubble?",
            chunks=chunks,
            answer="Bubble is een plugin.",
            expected_topics=["bubble", "browser-plugin"],
        )

    assert result["context_precision"] == pytest.approx(0.85)
    assert result["context_recall"] == pytest.approx(0.90)
    assert result["faithfulness"] == pytest.approx(0.88)
    assert result["answer_relevance"] == pytest.approx(0.92)

    # Each metric's ``ascore`` was called exactly once with the contract-shaped
    # kwargs.
    assert metrics["ContextPrecision"].calls == [
        {
            "user_input": "Hoe troubleshoot ik Bubble?",
            "reference": "bubble, browser-plugin",
            "retrieved_contexts": ["Context text"],
        }
    ]
    assert metrics["ContextRecall"].calls == [
        {
            "user_input": "Hoe troubleshoot ik Bubble?",
            "retrieved_contexts": ["Context text"],
            "reference": "bubble, browser-plugin",
        }
    ]
    assert metrics["Faithfulness"].calls == [
        {
            "user_input": "Hoe troubleshoot ik Bubble?",
            "response": "Bubble is een plugin.",
            "retrieved_contexts": ["Context text"],
        }
    ]
    # AnswerRelevancy contract is just (user_input, response) — embeddings
    # are configured at construction.
    assert metrics["AnswerRelevancy"].calls == [
        {
            "user_input": "Hoe troubleshoot ik Bubble?",
            "response": "Bubble is een plugin.",
        }
    ]


# ---------------------------------------------------------------------------
# evaluate_query — partial failure (one metric raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_partial_failure(monkeypatch, _patch_module_deps) -> None:
    """One metric raises → None for that metric; others survive (REQ-3)."""
    _install_fake_collections(
        monkeypatch,
        Faithfulness=_CannedMetric(None, raises=ValueError("malformed JSON")),
    )

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "Context text"}]

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await evaluate_query(
            query="Test query",
            chunks=chunks,
            answer="Test answer",
            expected_topics=["test"],
        )

    assert result["faithfulness"] is None
    assert result["context_precision"] == pytest.approx(0.85)
    assert result["context_recall"] == pytest.approx(0.90)
    assert result["answer_relevance"] == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# evaluate_query — empty chunks short-circuits to all-None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_no_chunks_returns_all_none() -> None:
    """No chunks → fail-fast with all metrics None (no LLM call attempted)."""
    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await evaluate_query(
            query="anything",
            chunks=[],
            answer="anything",
            expected_topics=["x"],
        )

    assert result == {
        "context_precision": None,
        "context_recall": None,
        "faithfulness": None,
        "answer_relevance": None,
    }


# ---------------------------------------------------------------------------
# evaluate_query — fail-open on construction error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_query_fail_open_on_setup_error(monkeypatch) -> None:
    """If RAGAS LLM construction blows up, return all-None instead of raising."""
    monkeypatch.setattr(
        "knowledge_ingest.eval.judge_client._build_ragas_llm",
        MagicMock(side_effect=RuntimeError("LiteLLM is down")),
    )

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()
    chunks = [{"id": "c1", "text": "ctx"}]

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        result = await evaluate_query(query="q", chunks=chunks, answer="a", expected_topics=["t"])

    assert result == {
        "context_precision": None,
        "context_recall": None,
        "faithfulness": None,
        "answer_relevance": None,
    }


# ---------------------------------------------------------------------------
# _safe_ascore unit
# ---------------------------------------------------------------------------


class TestSafeAscore:
    @pytest.mark.asyncio
    async def test_returns_value_on_success(self) -> None:
        from knowledge_ingest.eval.judge_client import _safe_ascore

        async def _coro():
            return SimpleNamespace(value=0.42)

        result = await _safe_ascore("metric_x", _coro())
        assert result == pytest.approx(0.42)

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        from knowledge_ingest.eval.judge_client import _safe_ascore

        async def _coro():
            raise RuntimeError("boom")

        result = await _safe_ascore("metric_y", _coro())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_value_missing(self) -> None:
        from knowledge_ingest.eval.judge_client import _safe_ascore

        async def _coro():
            return SimpleNamespace(value=None)

        result = await _safe_ascore("metric_z", _coro())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_value_non_numeric(self) -> None:
        from knowledge_ingest.eval.judge_client import _safe_ascore

        async def _coro():
            return SimpleNamespace(value="not-a-float")

        result = await _safe_ascore("metric_q", _coro())
        assert result is None


# ---------------------------------------------------------------------------
# Smoke: each metric runs exactly once via asyncio.gather (regression guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_run_in_parallel(monkeypatch, _patch_module_deps) -> None:
    """All 4 metrics' ascore coroutines must each be awaited exactly once."""
    metrics = _install_fake_collections(monkeypatch)

    from knowledge_ingest.eval.judge_client import evaluate_query

    settings = _make_settings()

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        await evaluate_query(
            query="q",
            chunks=[{"id": "c1", "text": "ctx"}],
            answer="a",
            expected_topics=["t"],
        )

    for name in (
        "ContextPrecision",
        "ContextRecall",
        "Faithfulness",
        "AnswerRelevancy",
    ):
        assert len(metrics[name].calls) == 1, f"{name} ascore was not called exactly once"


# ---------------------------------------------------------------------------
# Embeddings adapter regression guard
# ---------------------------------------------------------------------------


def test_build_ragas_embeddings_returns_canonical_modern_provider() -> None:
    """Regression: ``ragas.metrics.collections`` rejects custom embedding
    classes with::

        Collections metrics only support modern embeddings.
        Found: <CustomClass>. Use: embedding_factory('openai', ...,
        interface='modern')

    PR C originally shipped a custom ``_LiteLLMRagasEmbeddings`` adapter
    that satisfied the BaseRagasEmbedding *method* shape (aembed_text /
    aembed_texts / embed_text / embed_texts) but failed the RAGAS *type*
    check, producing all-None metrics on every eval row. The fix is to
    use ``embedding_factory(provider='openai', ..., interface='modern')``
    which returns the canonical
    ``ragas.embeddings.openai_provider.OpenAIEmbeddings``. This test
    locks the contract so a future refactor to a custom class will
    fail loud instead of silently zeroing out the eval matrix.
    """
    from knowledge_ingest.eval.judge_client import _build_ragas_embeddings

    settings = _make_settings()

    with patch("knowledge_ingest.eval.judge_client.settings", settings):
        emb = _build_ragas_embeddings()

    cls = type(emb)
    # Canonical RAGAS modern provider — anything outside this namespace
    # means we drifted back to a custom class.
    assert cls.__module__.startswith("ragas.embeddings."), (
        f"Expected RAGAS-canonical embeddings, got {cls.__module__}.{cls.__name__}"
    )
    # The factory returns OpenAIEmbeddings for provider='openai'.
    assert cls.__name__ == "OpenAIEmbeddings", f"Expected OpenAIEmbeddings, got {cls.__name__}"


# ---------------------------------------------------------------------------
# AsyncMock import sanity.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_mock_smoke() -> None:
    m = AsyncMock(return_value=1)
    assert await m() == 1
