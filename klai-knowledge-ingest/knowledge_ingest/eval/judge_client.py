"""LLM judge client for the RAGAS evaluation harness (SPEC-RAG-EVAL-001).

Two responsibilities:
  1. ``generate_answer``: generate a model answer via klai-fast (LiteLLM proxy)
     given a query and retrieved chunks.
  2. ``evaluate_query``: run the four RAGAS metrics with per-metric LLM and
     embeddings injection — light metrics on klai-fast, faithfulness on
     klai-medium (Mistral Medium 3.5), answer_relevancy on klai-fast +
     klai-bge-m3 (BGE-M3 via TEI).

Both functions are fail-open: any HTTP or RAGAS failure logs and returns
``None`` (or a metrics dict with None values) instead of raising (REQ-3
generalisation).

Per-metric model assignment:
  - context_precision  → klai-fast LLM
  - context_recall     → klai-fast LLM
  - faithfulness       → klai-medium LLM (Mistral Medium 3.5; klai-fast
                          truncates the multi-statement JSON output)
  - answer_relevancy   → klai-fast LLM + klai-bge-m3 (BGE-M3 via TEI)

RAGAS 0.4.3 ``ragas.metrics.collections`` API:
  - Each metric class is constructed with its dependencies (``llm``, optionally
    ``embeddings``) and exposes a per-metric ``ascore(...)`` coroutine with a
    metric-specific signature.
  - No more ``EvaluationDataset`` + synchronous ``evaluate()`` thread-pool
    dance. Each metric runs in parallel via ``asyncio.gather`` with a
    per-metric try/except so one failure does not poison the others.
  - Embeddings on the new path implement ``BaseRagasEmbedding``
    (``aembed_text`` / ``aembed_texts`` / ``embed_text`` / ``embed_texts``),
    not the legacy LangChain ``embed_query`` / ``embed_documents`` shape.
"""

from __future__ import annotations

import asyncio
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


# Faithfulness's NLI verdicts prompt produces a multi-statement JSON
# (one verdict object per atomic statement). RAGAS' default cap is
# 1024 tokens — too small once the answer has > 5 statements, which
# yields a truncated completion and "The output is incomplete due to
# a max_tokens length limit." parse failure. 8192 covers the longest
# Voys answers comfortably and is well within klai-medium's output
# budget. Light metrics (precision/recall, answer_relevancy) emit
# small JSON; the default cap is fine for them.
_FAITHFULNESS_MAX_TOKENS = 8192


def _build_ragas_llm(
    model: str | None = None,
    *,
    max_tokens: int | None = None,
):
    """Build a RAGAS LLM wrapper pointed at the LiteLLM proxy.

    Falls back to ``settings.rag_eval_judge_model`` when ``model`` is None,
    keeping the original generate_answer / light-metric path unchanged.
    Faithfulness gets the heavier Mistral Medium 3.5 via klai-medium AND
    a higher ``max_tokens`` so the multi-statement NLI verdicts prompt
    isn't truncated at 1024 tokens (RAGAS' default).
    """
    from ragas.llms import llm_factory

    extra: dict[str, int] = {}
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    return llm_factory(
        model or settings.rag_eval_judge_model,
        client=_make_async_openai_client(),
        **extra,
    )


def _build_ragas_embeddings():
    """Build the RAGAS embeddings adapter pointed at klai-bge-m3.

    Uses RAGAS' canonical ``embedding_factory(provider="openai", ...,
    interface="modern")`` which returns the
    ``ragas.embeddings.openai_provider.OpenAIEmbeddings`` instance that
    ``ragas.metrics.collections`` validates against. A custom class that
    only ducks the method shape is rejected with::

        Collections metrics only support modern embeddings.
        Found: <CustomClass>. Use: embedding_factory('openai', ...,
        interface='modern')

    klai-bge-m3 is the LiteLLM alias for BGE-M3 on TEI/gpu-01. Used by
    AnswerRelevancy to compare imaginary-question vectors with the user's
    actual question. The OpenAI client is pointed at the LiteLLM proxy so
    every embedding call routes through klai-bge-m3.
    """
    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory

    client = AsyncOpenAI(
        base_url=f"{settings.litellm_url}/v1",
        api_key=settings.litellm_api_key or "no-key",
    )
    return embedding_factory(
        provider="openai",
        model=settings.rag_eval_embeddings_model,
        client=client,
        interface="modern",
    )


def _metric_value(result: Any) -> float | None:
    """Extract a numeric score from a RAGAS ``MetricResult`` (or None)."""
    if result is None:
        return None
    value = getattr(result, "value", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _safe_ascore(metric_name: str, coro: Any) -> float | None:
    """Await a single metric's ``ascore()`` coroutine, fail-open per-metric.

    RAGAS' classifier prompts occasionally return malformed JSON or trip
    on edge cases (empty contexts, missing reference). Per REQ-3 a failure
    in one metric must not kill the others — log and return None.
    """
    try:
        result = await coro
    except Exception as exc:
        logger.warning(
            "rag_eval_metric_failed",
            metric=metric_name,
            error=str(exc)[:_MAX_REASON_LEN],
        )
        return None
    return _metric_value(result)


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

    Returns a dict with keys: ``context_precision``, ``context_recall``,
    ``faithfulness``, ``answer_relevance``. Each value is a float or None
    when that metric could not be computed.

    Uses RAGAS 0.4.3's ``ragas.metrics.collections`` per-metric ``ascore()``
    API: each metric runs as its own coroutine in parallel via
    ``asyncio.gather``, and per-metric errors are absorbed so one failure
    does not poison the others (REQ-3).

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
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )

        contexts = [c.get("text", "") for c in chunks]
        reference = ", ".join(expected_topics) if expected_topics else "general"
        response = answer or ""

        # Per-metric model assignment (see module docstring).
        light_llm = _build_ragas_llm()
        heavy_llm = _build_ragas_llm(
            model=settings.rag_eval_faithfulness_model,
            max_tokens=_FAITHFULNESS_MAX_TOKENS,
        )
        embeddings = _build_ragas_embeddings()

        ctx_precision = ContextPrecision(llm=light_llm)
        ctx_recall = ContextRecall(llm=light_llm)
        faithful = Faithfulness(llm=heavy_llm)
        answer_rel = AnswerRelevancy(llm=light_llm, embeddings=embeddings)

        # Run all four metrics in parallel — RAGAS' new API is async-native,
        # so no thread-pool dance. Per-metric try/except inside _safe_ascore
        # turns one metric's failure into a None entry instead of a raise.
        cp, cr, fa, ar = await asyncio.gather(
            _safe_ascore(
                "context_precision",
                ctx_precision.ascore(
                    user_input=query,
                    reference=reference,
                    retrieved_contexts=contexts,
                ),
            ),
            _safe_ascore(
                "context_recall",
                ctx_recall.ascore(
                    user_input=query,
                    retrieved_contexts=contexts,
                    reference=reference,
                ),
            ),
            _safe_ascore(
                "faithfulness",
                faithful.ascore(
                    user_input=query,
                    response=response,
                    retrieved_contexts=contexts,
                ),
            ),
            _safe_ascore(
                "answer_relevancy",
                answer_rel.ascore(user_input=query, response=response),
            ),
        )
        result["context_precision"] = cp
        result["context_recall"] = cr
        result["faithfulness"] = fa
        # RAGAS column is "answer_relevancy" but the eval store keeps the
        # historical "answer_relevance" key for the rag_eval_results schema.
        result["answer_relevance"] = ar

    except Exception as exc:
        logger.warning(
            "rag_eval_judge_metrics_failed",
            query=query[:80],
            error=str(exc)[:200],
        )

    return result
