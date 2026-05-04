"""
LLM judge client for the RAGAS evaluation harness (SPEC-RAG-EVAL-001).

Two responsibilities:
  1. generate_answer: generate a model answer via klai-fast (LiteLLM proxy)
     given a query and retrieved chunks.
  2. evaluate_query: run the four RAGAS metrics with klai-fast as the judge LLM.

Both functions are fail-open: any HTTP or RAGAS failure returns None / a
metrics dict with None values instead of raising (REQ-3 generalisation).

RAGAS 0.4.3 API notes:
  - Uses EvaluationDataset + SingleTurnSample (not HF datasets).
  - llm_factory(model, client=AsyncOpenAI(...)) is the recommended wrapper.
  - evaluate() is synchronous in 0.4.3; run it in a thread via asyncio.
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


def _build_ragas_llm():
    """Build the RAGAS LLM wrapper using llm_factory + AsyncOpenAI.

    Pointed at the LiteLLM proxy so klai-fast handles Mistral calls.
    llm_factory is the recommended approach in RAGAS 0.4.3.
    """
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory

    client = AsyncOpenAI(
        base_url=f"{settings.litellm_url}/v1",
        api_key=settings.litellm_api_key or "no-key",
    )
    return llm_factory(settings.rag_eval_judge_model, client=client)


async def _run_ragas_evaluate(dataset, metrics, llm):
    """Run ragas.evaluate in a thread pool to avoid blocking the event loop.

    RAGAS 0.4.3 evaluate() is synchronous. Separated into its own function
    so tests can patch it cleanly without touching the public API.
    """
    from ragas import evaluate

    loop = asyncio.get_event_loop()
    fn = functools.partial(
        evaluate,
        dataset,
        metrics=metrics,
        llm=llm,
        show_progress=False,
    )
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
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
        from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

        sample = SingleTurnSample(
            user_input=query,
            retrieved_contexts=[c.get("text", "") for c in chunks],
            response=answer or "",
            reference=", ".join(expected_topics) if expected_topics else "general",
        )
        dataset = EvaluationDataset(samples=[sample])

        metrics = [ContextPrecision(), ContextRecall(), Faithfulness(), AnswerRelevancy()]
        llm = _build_ragas_llm()

        eval_result = await _run_ragas_evaluate(dataset, metrics, llm)

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
