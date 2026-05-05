"""
LLM judge client for the RAGAS evaluation harness (SPEC-RAG-EVAL-001).

Two responsibilities:
  1. generate_answer: generate a model answer via klai-fast (LiteLLM proxy)
     given a query and retrieved chunks.
  2. evaluate_query: run the four RAGAS metrics with per-metric LLM/embeddings
     injection — light metrics on klai-fast, faithfulness on klai-medium
     (Mistral Medium 3.5), answer_relevancy embeddings on klai-embeddings (BGE-M3).

Both functions are fail-open: any HTTP or RAGAS failure returns None / a
metrics dict with None values instead of raising (REQ-3 generalisation).

Per-metric model assignment (post-baseline-fix 2026-05-05):
  - context_precision  → klai-fast LLM
  - context_recall     → klai-fast LLM
  - faithfulness       → klai-medium LLM (Mistral Medium 3.5; klai-fast
                          truncates the multi-statement JSON output)
  - answer_relevancy   → klai-fast LLM + klai-embeddings (BGE-M3 via TEI)

RAGAS 0.4.3 API notes:
  - Uses EvaluationDataset + SingleTurnSample (not HF datasets).
  - llm_factory(model, client=AsyncOpenAI(...)) is the recommended wrapper.
  - evaluate() is synchronous in 0.4.3; run it in a thread via asyncio.
  - Each metric class accepts an optional ``llm`` (and ``embeddings`` where
    applicable) constructor arg, which RAGAS uses in preference to any
    global llm/embeddings passed to evaluate().
  - context_precision / context_recall need reference (ground_truth).
  - faithfulness / answer_relevancy need response (model answer).
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

_MAX_REASON_LEN = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_http_client(
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {"timeout": timeout}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


def _make_async_openai_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        base_url=f"{settings.litellm_url}/v1",
        api_key=settings.litellm_api_key or "no-key",
    )


def _build_ragas_llm(model: str | None = None):
    """Build a RAGAS LLM wrapper pointed at the LiteLLM proxy.

    Falls back to ``settings.rag_eval_judge_model`` when ``model`` is None,
    keeping the original generate_answer / light-metric path unchanged.
    Faithfulness gets the heavier Mistral Medium 3.5 via klai-medium.
    """
    from ragas.llms import llm_factory

    return llm_factory(model or settings.rag_eval_judge_model, client=_make_async_openai_client())


class _SimpleEmbeddings:
    """Minimal embeddings adapter for RAGAS AnswerRelevancy.

    RAGAS 0.4.3's AnswerRelevancy metric calls ``embed_query(text)`` and
    ``embed_documents(list[text])`` on its embeddings object. The modern
    ``ragas.embeddings.OpenAIEmbeddings`` provider only exposes the new
    ``aembed_text`` / ``embed_text`` interface, not these legacy method
    names — so we cannot use it here yet. Rather than pull the deprecated
    ``LangchainEmbeddingsWrapper`` (which RAGAS will remove in v1.0), we
    implement the two methods directly against the OpenAI Python SDK
    pointed at our LiteLLM proxy.

    AnswerRelevancy is fully synchronous (calls embed_query inside a
    numpy.asarray(...) chain), so we use the sync OpenAI client to avoid
    event-loop juggling inside RAGAS' thread pool.
    """

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(input=text, model=self._model)
        return resp.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(input=texts, model=self._model)
        return [d.embedding for d in resp.data]


def _build_ragas_embeddings() -> _SimpleEmbeddings:
    """Build the RAGAS embeddings adapter pointed at klai-embeddings.

    klai-embeddings is the LiteLLM alias for BGE-M3 on TEI/gpu-01.
    Used by AnswerRelevancy to compare imaginary-question vectors with
    the user's actual question.
    """
    return _SimpleEmbeddings(
        base_url=f"{settings.litellm_url}/v1",
        api_key=settings.litellm_api_key or "no-key",
        model=settings.rag_eval_embeddings_model,
    )


async def _run_ragas_evaluate(dataset, metrics):
    """Run ragas.evaluate in a thread pool to avoid blocking the event loop.

    RAGAS 0.4.3 evaluate() is synchronous. The metrics list carries its own
    per-metric LLM/embeddings wrappers (set in evaluate_query), so evaluate()
    is called without a global llm/embeddings arg.

    Separated into its own function so tests can patch it cleanly without
    touching the public API.
    """
    from ragas import evaluate

    loop = asyncio.get_event_loop()
    fn = functools.partial(evaluate, dataset, metrics=metrics, show_progress=False)
    return await loop.run_in_executor(None, fn)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_answer(
    query: str,
    chunks: list[dict[str, Any]],
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """Generate a model answer via klai-fast given a query and retrieved chunks.

    Constructs a Dutch RAG prompt, POSTs to LiteLLM /v1/chat/completions,
    and returns the assistant content string. Returns None on any failure.

    Parameters
    ----------
    query:
        The natural-language question.
    chunks:
        Retrieved chunks -- each must have at least a ``text`` key.
    _transport:
        Optional httpx transport for test injection.
    """
    context_parts = [c.get("text", "") for c in chunks]
    context = "\n\n".join(context_parts)
    prompt = (
        "Beantwoord de vraag op basis van de bijgevoegde context.\n\n"
        f"Context:\n{context}\n\n"
        f"Vraag: {query}\n\n"
        "Antwoord:"
    )
    payload = {
        "model": settings.rag_eval_judge_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }
    api_key = settings.litellm_api_key or "no-key"
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{settings.litellm_url}/v1/chat/completions"

    try:
        async with _build_http_client(float(settings.rag_eval_judge_timeout), _transport) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("rag_eval_judge_answer_failed", query=query[:80], error=str(exc)[:200])
        return None


async def evaluate_query(
    query: str,
    chunks: list[dict[str, Any]],
    answer: str | None,
    expected_topics: list[str],
) -> dict[str, float | None]:
    """Run the four RAGAS metrics for one query and return a metrics dict.

    Returns a dict with keys: context_precision, context_recall,
    faithfulness, answer_relevance. Each value is a float or None when
    that metric could not be computed.

    Partial failures are handled gracefully: if one metric is absent from
    the RAGAS result scores, it returns None while others are preserved.

    Parameters
    ----------
    query:
        The natural-language question.
    chunks:
        Retrieved chunks with at least a ``text`` key each.
    answer:
        Model answer string (from generate_answer). May be None.
    expected_topics:
        Topic labels used as ground_truth for context_precision/recall.
    """
    result: dict[str, float | None] = {
        "context_precision": None,
        "context_recall": None,
        "faithfulness": None,
        "answer_relevance": None,
    }

    if not chunks:
        logger.warning("rag_eval_judge_no_chunks", query=query[:80])
        return result

    try:
        # TODO: migrate to ragas.metrics.collections.* + per-metric ascore()
        # when we revisit the harness API. The collections refactor is a full
        # API change (no more evaluate(dataset); per-metric ascore() calls
        # with different signatures), out of scope for this baseline-fix PR.
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
        from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

        sample = SingleTurnSample(
            user_input=query,
            retrieved_contexts=[c.get("text", "") for c in chunks],
            response=answer or "",
            reference=", ".join(expected_topics) if expected_topics else "general",
        )
        dataset = EvaluationDataset(samples=[sample])

        # Per-metric model assignment: light metrics on klai-fast, faithfulness
        # on klai-medium (Mistral Medium 3.5), answer_relevancy on klai-fast +
        # klai-embeddings (BGE-M3). See module docstring for rationale.
        light_llm = _build_ragas_llm()
        heavy_llm = _build_ragas_llm(model=settings.rag_eval_faithfulness_model)
        embeddings = _build_ragas_embeddings()

        metrics = [
            ContextPrecision(llm=light_llm),
            ContextRecall(llm=light_llm),
            Faithfulness(llm=heavy_llm),
            AnswerRelevancy(llm=light_llm, embeddings=embeddings),
        ]

        eval_result = await _run_ragas_evaluate(dataset, metrics)

        # scores is a list of dicts, one per sample
        if eval_result.scores:
            scores = eval_result.scores[0]
            result["context_precision"] = scores.get("context_precision")
            result["context_recall"] = scores.get("context_recall")
            result["faithfulness"] = scores.get("faithfulness")
            # RAGAS key is "answer_relevancy" but we store as "answer_relevance"
            result["answer_relevance"] = scores.get("answer_relevancy")

    except Exception as exc:
        logger.warning(
            "rag_eval_judge_metrics_failed",
            query=query[:80],
            error=str(exc)[:200],
        )

    return result
