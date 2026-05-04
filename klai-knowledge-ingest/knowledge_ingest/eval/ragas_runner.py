"""
Procrastinate task for the nightly RAGAS evaluation harness (SPEC-RAG-EVAL-001).

Per-query flow (REQ-1, REQ-2, REQ-3):
    1. Load suite YAML via suite_loader.
    2. For each query: call /retrieve on klai-retrieval-api.
       On failure: write a NULL-metric row with meta.error and continue.
    3. Generate model answer via klai-fast.
    4. Run 4 RAGAS metrics via klai-fast as judge.
    5. Write one row to knowledge.rag_eval_results.
    6. Emit per-query structured log.

Concurrency contract:
    queueing_lock=f"rag-eval-{suite}" ensures at most one evaluation per suite
    at any time (REQ-5).

Variant routing (REQ-6):
    RAG_EVAL_VARIANT env var, default baseline.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import structlog

from knowledge_ingest import queues
from knowledge_ingest.config import settings

logger = structlog.get_logger()

_MAX_ERROR_LEN = 200


async def run_evaluation(suite: str, variant: str | None = None) -> dict:
    """Run the RAGAS evaluation harness for one query suite.

    Public entry point shared by both the Procrastinate task wrapper AND the
    ad-hoc CLI (REQ-7). When ``variant`` is None, falls back to the
    RAG_EVAL_VARIANT env var (default 'baseline') per REQ-6.
    """
    from knowledge_ingest.eval import judge_client, retrieval_client
    from knowledge_ingest.eval.retrieval_client import RetrievalFailure
    from knowledge_ingest.eval.store import insert_eval_row
    from knowledge_ingest.eval.suite_loader import load_suite

    if variant is None:
        variant = os.getenv("RAG_EVAL_VARIANT", "baseline")
    t_start = time.monotonic()

    logger.info("rag_eval_run_started", suite=suite, variant=variant)

    suites_dir = Path(settings.rag_eval_suites_dir)
    suite_file = suites_dir / f"{suite}.yaml"
    loaded = load_suite(suite_file)

    queries_processed: int = 0
    rows_written: int = 0

    for query in loaded.queries:
        q_errors: list[str] = []
        meta: dict[str, Any] = {
            "variant": variant,
            "errors": q_errors,
        }

        retrieval = await retrieval_client.retrieve_chunks(
            query=query.query,
            org_zitadel_id=query.org_zitadel_id,
            user_zitadel_id=query.user_zitadel_id,
        )

        if isinstance(retrieval, RetrievalFailure):
            reason = retrieval.reason[:_MAX_ERROR_LEN]
            meta["error"] = f"retrieval_failed: {reason}"
            await insert_eval_row(
                suite=suite,
                variant=variant,
                query_id=query.id,
                context_precision=None,
                context_recall=None,
                faithfulness=None,
                answer_relevance=None,
                retrieved_chunk_ids=[],
                retrieval_ms=None,
                total_tokens=None,
                meta=meta,
            )
            rows_written += 1
            queries_processed += 1
            logger.info(
                "rag_eval_query_evaluated",
                query_id=query.id,
                suite=suite,
                variant=variant,
                context_precision=None,
                context_recall=None,
                faithfulness=None,
                answer_relevance=None,
                retrieval_ms=None,
                error_count=1,
            )
            continue

        chunks = retrieval.chunks
        retrieval_ms = retrieval.retrieval_ms
        total_tokens = retrieval.total_tokens
        chunk_ids = [c.get("id", "") for c in chunks if c.get("id")]

        answer = await judge_client.generate_answer(
            query=query.query,
            chunks=chunks,
        )
        if answer is None:
            q_errors.append("judge_answer_failed")

        metrics = await judge_client.evaluate_query(
            query=query.query,
            chunks=chunks,
            answer=answer,
            expected_topics=query.expected_topics,
        )

        await insert_eval_row(
            suite=suite,
            variant=variant,
            query_id=query.id,
            context_precision=metrics.get("context_precision"),
            context_recall=metrics.get("context_recall"),
            faithfulness=metrics.get("faithfulness"),
            answer_relevance=metrics.get("answer_relevance"),
            retrieved_chunk_ids=chunk_ids,
            retrieval_ms=retrieval_ms,
            total_tokens=total_tokens,
            meta=meta,
        )
        rows_written += 1
        queries_processed += 1

        logger.info(
            "rag_eval_query_evaluated",
            query_id=query.id,
            suite=suite,
            variant=variant,
            context_precision=metrics.get("context_precision"),
            context_recall=metrics.get("context_recall"),
            faithfulness=metrics.get("faithfulness"),
            answer_relevance=metrics.get("answer_relevance"),
            retrieval_ms=retrieval_ms,
            error_count=len(q_errors),
        )

    duration_ms = int((time.monotonic() - t_start) * 1000)
    logger.info(
        "rag_eval_run_completed",
        suite=suite,
        variant=variant,
        queries_processed=queries_processed,
        rows_written=rows_written,
        duration_ms=duration_ms,
    )
    return {
        "suite": suite,
        "variant": variant,
        "queries_processed": queries_processed,
        "rows_written": rows_written,
    }


def register_eval_tasks(procrastinate_app: Any) -> None:
    """Register the nightly RAGAS evaluation task on the Procrastinate app."""
    import procrastinate

    @procrastinate_app.task(
        queue=queues.RAG_EVAL,
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def evaluate_retrieval_quality_nightly(suite: str) -> dict:
        """Procrastinate-task wrapper around run_evaluation()."""
        return await run_evaluation(suite=suite)

    procrastinate_app.evaluate_retrieval_quality_nightly = evaluate_retrieval_quality_nightly  # type: ignore[attr-defined]
