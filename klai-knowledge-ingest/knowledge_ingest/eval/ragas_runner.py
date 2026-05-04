"""
Procrastinate task registration for the nightly RAGAS evaluation harness.

This module is the sole public entrypoint for wiring the eval task into the
Procrastinate worker. Call ``register_eval_tasks(procrastinate_app)`` from
``enrichment_tasks.init_app()`` alongside the other task-module registrations.

Why it exists:
    Retrieval-quality regressions are invisible without instrumented metrics.
    The nightly ``evaluate_retrieval_quality_nightly`` task provides RAGAS
    scores (context_precision, context_recall, faithfulness, answer_relevance)
    written to ``knowledge.rag_eval_results``, which Grafana surfaces as a
    7-day moving average per suite.  Unit 3 fills in the retrieval + metric
    logic; this module establishes the orchestration shell — scheduling,
    concurrency locking, variant tagging, and structured logging — so that
    wiring and locking are proven correct before the heavier work lands.

Concurrency contract:
    ``queueing_lock=f"rag-eval-{suite}"`` ensures at most one pending or
    running evaluation exists per suite at any time.  A second ``.defer()``
    against the same suite raises ``procrastinate.exceptions.AlreadyEnqueued``
    (Procrastinate's deferral-rejection mechanism), satisfying REQ-5.

Variant routing (REQ-6):
    The env var ``RAG_EVAL_VARIANT`` is read once per task invocation.
    Default is ``'baseline'``.  Experiment branches set this var before the
    nightly cron fires.  Every result row carries the same variant value.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

from knowledge_ingest import queues

logger = structlog.get_logger()


def register_eval_tasks(procrastinate_app: Any) -> None:
    """Register the nightly RAGAS evaluation task on the Procrastinate app.

    Mirror of the ``register_clustering_tasks`` / ``register_crawl_tasks``
    pattern used by every other task module in this service.  Called once
    from ``enrichment_tasks.init_app()`` after the DB pool is ready.

    The registered task (``evaluate_retrieval_quality_nightly``) is
    skeletal in Unit 2: it emits structured log events and returns a
    zero-count dict.  Unit 3 replaces the stub body with real retrieval
    calls and RAGAS metric computation.
    """
    import procrastinate

    @procrastinate_app.task(
        queue=queues.RAG_EVAL,
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def evaluate_retrieval_quality_nightly(suite: str) -> dict:
        """Run the RAGAS evaluation harness for one query suite.

        Parameters
        ----------
        suite:
            Name of the query suite to evaluate (e.g. ``'chat'``,
            ``'knowledge_org'``).  Determines the lock key so different
            suites can run in parallel while the same suite cannot collide
            with itself (REQ-5).
        """
        variant = os.getenv("RAG_EVAL_VARIANT", "baseline")
        t_start = time.monotonic()

        logger.info("rag_eval_run_started", suite=suite, variant=variant)

        # --- Unit 3 will replace these stubs with real retrieval + RAGAS ---
        queries_processed: int = 0
        rows_written: int = 0
        # -------------------------------------------------------------------

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

    # Expose via app attribute so callers (e.g. tests, __main__) can reach
    # the task object without importing this module directly.
    procrastinate_app.evaluate_retrieval_quality_nightly = evaluate_retrieval_quality_nightly  # type: ignore[attr-defined]
