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

import asyncio
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from knowledge_ingest import queues
from knowledge_ingest.config import settings

logger = structlog.get_logger()

_MAX_ERROR_LEN = 200
_MIN_BODY_CANARY_CHARS = 16
_INGEST_GAP_SUITE = "ingest_gap_canary"
_INGEST_GAP_LOCK = "ingest-gap-canary"
_INGEST_GAP_RUNTIME_S = 300
_INGEST_GAP_PERSIST_RESERVE_S = 30
_INGEST_GAP_LIMIT = 10
_INGEST_GAP_SCROLL_LIMIT = 64


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _rotated(values: list[Any], run_day: date, *, salt: str = "") -> list[Any]:
    if not values:
        return []
    offset = (run_day.toordinal() + int(_hash(salt)[:8], 16)) % len(values)
    return values[offset:] + values[:offset]


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
    retrieval_failures: int = 0

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
            kb_slugs=query.kb_slugs,
        )

        if isinstance(retrieval, RetrievalFailure):
            retrieval_failures += 1
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
    if retrieval_failures:
        logger.error(
            "rag_eval_run_failed",
            suite=suite,
            variant=variant,
            queries_processed=queries_processed,
            rows_written=rows_written,
            retrieval_failures=retrieval_failures,
            duration_ms=duration_ms,
        )
        raise RuntimeError(f"rag_eval_retrieval_failed: {retrieval_failures}/{queries_processed}")

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


async def _scope_candidates(scope: dict[str, str], run_day: date, deadline: float) -> list[dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from knowledge_ingest import qdrant_store

    async with asyncio.timeout_at(deadline):
        points, _ = await qdrant_store.get_client().scroll(
            collection_name=qdrant_store.COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="org_id", match=MatchValue(value=scope["org_id"])),
                    FieldCondition(key="kb_slug", match=MatchValue(value=scope["kb_slug"])),
                ]
            ),
            limit=_INGEST_GAP_SCROLL_LIMIT,
            with_payload=True,
            with_vectors=False,
        )

    by_artifact: dict[str, list[dict]] = defaultdict(list)
    for point in points:
        payload = point.payload or {}
        text = payload.get("text")
        questions = payload.get("questions")
        if payload.get("user_id") or not isinstance(text, str) or not text:
            continue
        if not isinstance(questions, list):
            continue
        chunk_id = str(point.id)
        artifact_id = payload.get("artifact_id")
        artifact_id = artifact_id if isinstance(artifact_id, str) and artifact_id else chunk_id
        for question in questions:
            if not isinstance(question, str) or not question.strip():
                continue
            by_artifact[artifact_id].append(
                {
                    "question": question.strip(),
                    "chunk": {
                        "chunk_id": chunk_id,
                        "artifact_id": artifact_id,
                        "text": text,
                        "questions": [question.strip()],
                    },
                }
            )

    salt = f"{scope['org_id']}:{scope['kb_slug']}"
    artifacts = _rotated(sorted(by_artifact), run_day, salt=salt)
    for artifact_id in artifacts:
        by_artifact[artifact_id].sort(
            key=lambda item: _hash([run_day.isoformat(), item["question"]])
        )
    ordered: list[dict] = []
    while any(by_artifact.values()):
        for artifact_id in artifacts:
            if by_artifact[artifact_id]:
                ordered.append(by_artifact[artifact_id].pop(0))
    return ordered


async def _insert_gap_row(*, query_id: str, source_ids: list[str], meta: dict) -> None:
    from knowledge_ingest.eval.store import insert_eval_row

    await insert_eval_row(
        suite=_INGEST_GAP_SUITE,
        variant="source_derived_v1",
        query_id=query_id,
        context_precision=None,
        context_recall=None,
        faithfulness=None,
        answer_relevance=None,
        retrieved_chunk_ids=source_ids,
        retrieval_ms=None,
        total_tokens=None,
        meta=meta,
    )


async def _run_ingest_gap_canary(*, run_day: date | None, limit: int) -> dict:
    if not 1 <= limit <= _INGEST_GAP_LIMIT:
        raise ValueError(f"limit must be between 1 and {_INGEST_GAP_LIMIT}")
    run_day = run_day or datetime.now(UTC).date()
    started = time.monotonic()
    deadline = (
        asyncio.get_running_loop().time() + _INGEST_GAP_RUNTIME_S - _INGEST_GAP_PERSIST_RESERVE_S
    )
    run_hash = _hash([run_day.isoformat(), _INGEST_GAP_SUITE])
    headers = {"Authorization": f"Bearer {settings.portal_internal_token}"}
    failures = 0
    omitted_scopes = 0
    status_counts: Counter[str] = Counter()
    candidates: dict[tuple[str, str], list[dict]] = {}

    try:
        async with httpx.AsyncClient(timeout=_INGEST_GAP_RUNTIME_S) as client:
            async with asyncio.timeout_at(deadline):
                response = await client.get(
                    f"{settings.portal_url}/internal/ingest-gap-eval/scopes", headers=headers
                )
            response.raise_for_status()
            raw_scopes = response.json()
            if not isinstance(raw_scopes, list):
                raise TypeError("eligible scopes response must be a list")
            scopes = [
                scope
                for scope in raw_scopes
                if isinstance(scope, dict)
                and isinstance(scope.get("org_id"), str)
                and isinstance(scope.get("kb_slug"), str)
            ]
            scopes = _rotated(
                sorted(scopes, key=lambda item: (item["org_id"], item["kb_slug"])), run_day
            )

            for index, scope in enumerate(scopes):
                scope_key = (scope["org_id"], scope["kb_slug"])
                try:
                    candidates[scope_key] = await _scope_candidates(scope, run_day, deadline)
                except Exception as exc:
                    failures += 1
                    status_counts["failed"] += 1
                    omitted_scopes += len(scopes) - index
                    await _insert_gap_row(
                        query_id=_hash([run_hash, scope_key, "source_load_failed"]),
                        source_ids=[],
                        meta={
                            "run_hash": run_hash,
                            "scope_hash": _hash(scope_key),
                            "status": "failed",
                            "stage": "source_load",
                            "error_type": type(exc).__name__,
                        },
                    )
                    break

            selected: list[tuple[tuple[str, str], dict]] = []
            queues = {key: list(items) for key, items in candidates.items()}
            while len(selected) < limit and any(queues.values()):
                for scope_key in queues:
                    if queues[scope_key] and len(selected) < limit:
                        selected.append((scope_key, queues[scope_key].pop(0)))

            by_scope: dict[tuple[str, str], list[dict]] = defaultdict(list)
            for scope_key, candidate in selected:
                by_scope[scope_key].append(candidate)
            attempted = len(selected)
            for scope_key, scope_candidates in by_scope.items():
                org_id, kb_slug = scope_key
                snapshot = {
                    "snapshot_id": _hash([run_hash, scope_key]),
                    "chunks": [candidate["chunk"] for candidate in scope_candidates],
                }
                try:
                    remaining = deadline - asyncio.get_running_loop().time()
                    model_budget = remaining - _INGEST_GAP_PERSIST_RESERVE_S
                    if model_budget <= 0:
                        raise TimeoutError
                    snapshot["budget_seconds"] = model_budget
                    async with asyncio.timeout_at(deadline):
                        endpoint = (
                            f"{settings.portal_url}/internal/ingest-gap-eval/"
                            f"{quote(org_id, safe='')}/{quote(kb_slug, safe='')}"
                        )
                        response = await client.post(
                            endpoint,
                            headers=headers,
                            json=snapshot,
                        )
                    response.raise_for_status()
                    assessment = response.json()
                    results = assessment.get("results") if isinstance(assessment, dict) else None
                    if not isinstance(results, list) or len(results) != len(scope_candidates):
                        raise TypeError("assessment response result count does not match request")
                    for result in results:
                        status_value = result.get("status", "failed")
                        status_counts[status_value] += 1
                        if status_value == "failed":
                            failures += 1
                        source_ids = [
                            value
                            for value in [result.get("source_chunk_id")]
                            if isinstance(value, str)
                        ]
                        await _insert_gap_row(
                            query_id=str(
                                result.get("question_id") or _hash([run_hash, source_ids])
                            ),
                            source_ids=source_ids,
                            meta={
                                "run_hash": run_hash,
                                "snapshot_hash": assessment.get("snapshot_hash"),
                                "scope_hash": assessment.get("scope_hash"),
                                "question_hash": result.get("question_id"),
                                "source_content_hash": result.get("source_content_hash"),
                                "analyzer_version": assessment.get("analyzer_version"),
                                "judge_model": assessment.get("judge_model"),
                                "status": status_value,
                                "present_diagnosis": result.get("present_diagnosis"),
                                "withheld_diagnosis": result.get("withheld_diagnosis"),
                                "error_type": result.get("error_type"),
                                "http_status": result.get("http_status"),
                                "source_ids": {
                                    "chunk": result.get("source_chunk_id"),
                                    "artifact": result.get("source_artifact_id"),
                                },
                            },
                        )
                except Exception as exc:
                    failures += len(scope_candidates)
                    http_status = (
                        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    )
                    for candidate in scope_candidates:
                        status_counts["failed"] += 1
                        chunk = candidate["chunk"]
                        await _insert_gap_row(
                            query_id=_hash(candidate["question"]),
                            source_ids=[chunk["chunk_id"]],
                            meta={
                                "run_hash": run_hash,
                                "scope_hash": _hash(scope_key),
                                "question_hash": _hash(candidate["question"]),
                                "source_content_hash": _hash(chunk["text"]),
                                "status": "failed",
                                "stage": "assessment",
                                "error_type": type(exc).__name__,
                                "http_status": http_status,
                                "source_ids": {
                                    "chunk": chunk["chunk_id"],
                                    "artifact": chunk["artifact_id"],
                                },
                            },
                        )
    except Exception as exc:
        failures += 1
        status_counts["failed"] += 1
        await _insert_gap_row(
            query_id=_hash([run_hash, "scope_discovery_failed"]),
            source_ids=[],
            meta={
                "run_hash": run_hash,
                "status": "failed",
                "stage": "scope_discovery",
                "error_type": type(exc).__name__,
            },
        )
        scopes = []
        selected = []
        attempted = 0

    eligible = sum(len(items) for items in candidates.values())
    scored = status_counts["detected_missing"] + status_counts["withheld_claimed_present"]
    omitted = max(0, eligible - attempted)
    quality_failures = status_counts["withheld_claimed_present"]
    summary = {
        "run_hash": run_hash,
        "eligible_scopes": len(scopes),
        "sampled_scopes": len({scope_key for scope_key, _ in selected}),
        "omitted_scopes": omitted_scopes,
        "eligible": eligible,
        "attempted": attempted,
        "scored": scored,
        "unscorable": status_counts["unscorable"],
        "failed": failures,
        "omitted": omitted,
        "coverage_status": "bounded_sample",
        "candidate_scan_limit_per_scope": _INGEST_GAP_SCROLL_LIMIT,
        "execution_ceiling": limit,
        "runtime_ceiling_seconds": _INGEST_GAP_RUNTIME_S,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "task_status": "failed" if failures else "completed",
        "quality_status": "failed" if quality_failures else "inconclusive",
        "status_counts": dict(sorted(status_counts.items())),
    }
    await _insert_gap_row(query_id=run_hash, source_ids=[], meta=summary)
    if failures:
        raise RuntimeError(f"ingest_gap_canary_failed: {failures}/{attempted}")
    return summary


async def run_ingest_gap_canary(
    *, run_day: date | None = None, limit: int = _INGEST_GAP_LIMIT
) -> dict:
    """Run the source-derived probe inside one five-minute wall-clock budget."""
    async with asyncio.timeout(_INGEST_GAP_RUNTIME_S):
        return await _run_ingest_gap_canary(run_day=run_day, limit=limit)


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

    @procrastinate_app.task(
        queue=queues.RAG_EVAL,
        retry=False,
        lock=_INGEST_GAP_LOCK,
        queueing_lock=_INGEST_GAP_LOCK,
    )
    async def evaluate_ingest_gap_canary_nightly() -> dict:
        return await run_ingest_gap_canary()

    procrastinate_app.evaluate_ingest_gap_canary_nightly = evaluate_ingest_gap_canary_nightly  # type: ignore[attr-defined]

    @procrastinate_app.periodic(cron="30 2 * * *", periodic_id=_INGEST_GAP_LOCK)
    @procrastinate_app.task(
        name="knowledge_ingest.eval.ragas_runner.evaluate_ingest_gap_canary_periodic",
        queue=queues.RAG_EVAL,
        retry=False,
        lock=_INGEST_GAP_LOCK,
        queueing_lock=_INGEST_GAP_LOCK,
    )
    async def evaluate_ingest_gap_canary_periodic(timestamp: int) -> dict:
        logger.info("ingest_gap_canary_periodic_fired", deferrer_ts=timestamp)
        return await run_ingest_gap_canary()

    procrastinate_app.evaluate_ingest_gap_canary_periodic = evaluate_ingest_gap_canary_periodic  # type: ignore[attr-defined]

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
