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


_MIN_BODY_CANARY_CHARS = 16


def _chunk_canary_fields(chunk: dict[str, Any]) -> tuple[str, str]:
    """Return strong fields and body text for expected_chunks canary matching."""
    strong_values = [
        chunk.get("chunk_id"),
        chunk.get("id"),
        chunk.get("title"),
        chunk.get("source_url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        strong_values.extend(
            [
                metadata.get("title"),
                metadata.get("source_url"),
                metadata.get("path"),
                metadata.get("kb_slug"),
            ]
        )
    strong = "\n".join(str(v) for v in strong_values if v is not None).lower()
    body = str(chunk.get("text") or "").lower()
    return strong, body


def _canary_allows_body_match(expected: str) -> bool:
    """Body-text canaries must be specific enough to avoid generic hits."""
    marker = expected.strip()
    return len(marker) >= _MIN_BODY_CANARY_CHARS and any(ch.isspace() for ch in marker)


def _expected_chunk_canary(
    expected_chunks: list[str],
    chunks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare expected chunk markers with retrieved chunks.

    Suite YAML canaries are human-readable markers rather than guaranteed
    internal IDs, so matching is case-insensitive substring matching across
    stable returned fields. Empty expected_chunks means the query is not a
    canary and returns None.
    """
    if not expected_chunks:
        return None

    searchable_chunks = [_chunk_canary_fields(chunk) for chunk in chunks]
    matched: list[str] = []
    missing: list[str] = []
    for expected in expected_chunks:
        needle = expected.strip().lower()
        allow_body = _canary_allows_body_match(expected)
        if needle and any(
            needle in strong or (allow_body and needle in body)
            for strong, body in searchable_chunks
        ):
            matched.append(expected)
        else:
            missing.append(expected)

    return {
        "expected_chunks": expected_chunks,
        "matched_chunks": matched,
        "missing_chunks": missing,
        "passed": not missing,
    }


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
    loaded = load_suite(suite_file, require_reference_answer=True)

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
        # Retrieval-api emits chunks keyed on ``chunk_id`` (see ChunkResult
        # in retrieval_api/models.py). The earlier ``c.get("id")`` lookup
        # quietly returned None on every chunk, leaving the
        # retrieved_chunk_ids column empty on every eval row and breaking
        # the Grafana per-chunk drill-down for any post-mortem analysis.
        chunk_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]

        canary = _expected_chunk_canary(query.expected_chunks, chunks)
        if canary is not None:
            meta["canary"] = canary
        if canary is not None and not canary["passed"]:
            missing = ", ".join(canary["missing_chunks"])
            q_errors.append(f"canary_failed: missing expected_chunks: {missing}")
            await insert_eval_row(
                suite=suite,
                variant=variant,
                query_id=query.id,
                context_precision=None,
                context_recall=None,
                faithfulness=None,
                answer_relevance=None,
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
                context_precision=None,
                context_recall=None,
                faithfulness=None,
                answer_relevance=None,
                retrieval_ms=retrieval_ms,
                error_count=len(q_errors),
                canary_passed=False,
                canary_missing_chunks=canary["missing_chunks"],
            )
            continue

        meta["reference_source"] = "reference_answer"
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
            reference_answer=query.reference_answer or "",
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
    """Register the nightly RAGAS evaluation task on the Procrastinate app.

    Two registrations per suite:

    1. ``evaluate_retrieval_quality_nightly`` — the regular task. Operators
       can defer it ad-hoc (or it gets called by the periodic wrapper below).
    2. ``evaluate_retrieval_quality_periodic_<suite>`` — a thin periodic
       wrapper per suite, scheduled via Procrastinate's ``@app.periodic``
       decorator at 02:00 UTC daily. The PeriodicDeferrer lives inside the
       Procrastinate worker and fires the task on its cron schedule. No
       host-level cron job needed; ops just runs the worker as it already
       does.

    Why per-suite wrappers: ``@app.periodic`` cannot be parameterised at
    schedule time — the decorated task must take only the auto-injected
    ``timestamp`` arg. So we spell out one periodic registration per suite
    name, each calling ``run_evaluation(suite=...)`` internally.

    Cron schedule: ``"0 2 * * *"`` — 02:00 UTC daily. Picked to land after
    most timezone-spread chat traffic settles and before European business
    hours start; matches the placeholder in the runbook + Grafana alert
    description.
    """
    import procrastinate

    @procrastinate_app.task(
        queue=queues.RAG_EVAL,
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def evaluate_retrieval_quality_nightly(suite: str) -> dict:
        """Procrastinate-task wrapper around run_evaluation()."""
        return await run_evaluation(suite=suite)

    procrastinate_app.evaluate_retrieval_quality_nightly = evaluate_retrieval_quality_nightly  # type: ignore[attr-defined]

    # Periodic wrappers — one per suite. PeriodicDeferrer in the worker
    # picks them up and defers ``evaluate_retrieval_quality_nightly``
    # automatically at 02:00 UTC every day.
    #
    # Each suite needs its own task with a unique ``name=`` because
    # Procrastinate registers tasks under their function name by default —
    # two ``_periodic_eval`` definitions in a for-loop would collide
    # (TaskAlreadyRegistered). Explicit names also keep the worker logs
    # self-explanatory.
    def _make_periodic_wrapper(suite_name: str):
        """Build a periodic-task wrapper bound to one suite.

        Closure captures ``suite_name`` cleanly via the function argument —
        avoids the late-binding gotcha of closing over a loop variable.
        """

        @procrastinate_app.periodic(
            cron="0 2 * * *",
            periodic_id=f"rag-eval-{suite_name}",
        )
        @procrastinate_app.task(
            name=f"knowledge_ingest.eval.ragas_runner.evaluate_retrieval_quality_periodic_{suite_name}",
            queue=queues.RAG_EVAL,
            retry=procrastinate.RetryStrategy(max_attempts=1),
            # queueing_lock at the periodic task level mirrors the existing
            # ad-hoc lock so an in-flight nightly + ad-hoc trigger can't
            # double-run the same suite.
            queueing_lock=f"rag-eval-{suite_name}",
        )
        async def _periodic_eval(timestamp: int) -> dict:
            """PeriodicDeferrer-managed wrapper.

            ``timestamp`` is the Unix epoch when the deferrer fired (auto-
            injected by Procrastinate's periodic mechanism). Logged but
            not used for the run itself.
            """
            logger.info("rag_eval_periodic_fired", suite=suite_name, deferrer_ts=timestamp)
            return await run_evaluation(suite=suite_name)

        return _periodic_eval

    for suite in ("chat", "knowledge_org"):
        _make_periodic_wrapper(suite)
