"""
Tests for knowledge_ingest.eval.ragas_runner.

RED phase: all tests fail until the implementation module exists.

Coverage:
  - REQ-5: queueing_lock prevents parallel runs per suite
  - REQ-6: variant read from RAG_EVAL_VARIANT env var, default 'baseline'
  - Procrastinate task registration on the rag-eval queue
  - Structured log events emitted by the task body
  - Queue placement in the LLM lane
"""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_NAME = "knowledge_ingest.eval.ragas_runner.evaluate_retrieval_quality_nightly"
_GAP_TASK_NAME = "knowledge_ingest.eval.ragas_runner.evaluate_ingest_gap_canary_nightly"


def _make_app():
    """Return a Procrastinate App backed by an InMemoryConnector.

    psycopg is stubbed via conftest.py so procrastinate.testing imports work
    on dev hosts without libpq.
    """
    from procrastinate import App
    from procrastinate.testing import InMemoryConnector

    connector = InMemoryConnector()
    app = App(connector=connector)
    app.open()
    return app, connector


# ---------------------------------------------------------------------------
# Test 1 - task registration
# ---------------------------------------------------------------------------


def test_register_eval_tasks_registers_nightly_task():
    """register_eval_tasks() must register evaluate_retrieval_quality_nightly
    on the 'rag-eval' queue.

    Procrastinate registers tasks under their fully qualified Python name.
    We also verify the task is accessible via the convenience attribute that
    register_eval_tasks() attaches to the app.
    """
    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    app, _connector = _make_app()
    register_eval_tasks(app)

    assert _TASK_NAME in app.tasks
    task = app.tasks[_TASK_NAME]
    assert task.queue == "rag-eval", f"expected queue='rag-eval', got queue={task.queue!r}"
    assert hasattr(app, "evaluate_retrieval_quality_nightly")


def test_register_eval_tasks_registers_periodic_schedule():
    """register_eval_tasks() must schedule one periodic deferral per suite
    so the PeriodicDeferrer fires the nightly run at 02:00 UTC without any
    host-level cron job.

    The original v1 of the harness left this gap intentionally — the task
    was registered but never scheduled, requiring an operator to defer
    every night. This test locks the contract so a future refactor can't
    silently drop the periodic registration and turn the dashboard into a
    flat line again.
    """
    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    app, _connector = _make_app()
    register_eval_tasks(app)

    # Procrastinate stores periodic registrations on the registry.
    periodic_entries = list(app.periodic_registry.periodic_tasks.values())
    suite_ids = {entry.periodic_id for entry in periodic_entries}

    assert "rag-eval-chat" in suite_ids, (
        f"expected periodic registration for 'rag-eval-chat', got {suite_ids}"
    )
    assert "rag-eval-knowledge_org" in suite_ids, (
        f"expected periodic registration for 'rag-eval-knowledge_org', got {suite_ids}"
    )
    assert "ingest-gap-canary" in suite_ids
    gap_entry = next(
        entry for entry in periodic_entries if entry.periodic_id == "ingest-gap-canary"
    )
    assert "30 2" in str(gap_entry.cron)

    # All periodic entries must run at 02:00 UTC (cron "0 2 * * *").
    for entry in periodic_entries:
        if entry.periodic_id.startswith("rag-eval-"):
            cron_str = str(entry.cron)
            assert "0 2" in cron_str, (
                f"expected '0 2 ...' cron for {entry.periodic_id}, got {cron_str!r}"
            )


def test_ingest_gap_task_has_waiting_and_running_locks() -> None:
    from procrastinate.jobs import Job

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    app, _connector = _make_app()
    register_eval_tasks(app)

    tasks = [
        app.tasks[_GAP_TASK_NAME],
        app.tasks["knowledge_ingest.eval.ragas_runner.evaluate_ingest_gap_canary_periodic"],
    ]
    assert all(task.queue == "rag-eval" for task in tasks)
    assert all(task.queueing_lock == "ingest-gap-canary" for task in tasks)
    assert all(task.lock == "ingest-gap-canary" for task in tasks)
    for task in tasks:
        job = Job(
            queue=task.queue, task_name=task.name, lock=task.lock, queueing_lock=task.queueing_lock
        )
        assert task.get_retry_exception(TimeoutError(), job) is None


# ---------------------------------------------------------------------------
# Test 2 - variant from env var
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nightly_task_uses_env_var_for_variant():
    """REQ-6: task body reads RAG_EVAL_VARIANT and surfaces it in the
    rag_eval_run_started structured log event.
    """
    import structlog.testing

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    app, connector = _make_app()
    register_eval_tasks(app)

    # Mock load_suite so the task body doesn't need a real YAML file.
    from knowledge_ingest.eval.suite_loader import Suite

    mock_suite = Suite(name="chat", description="", queries=[])

    try:
        os.environ["RAG_EVAL_VARIANT"] = "contextual_v1"
        with structlog.testing.capture_logs() as captured:
            with patch("knowledge_ingest.eval.suite_loader.load_suite", return_value=mock_suite):
                await app.tasks[_TASK_NAME].defer_async(suite="chat")
                jobs = list(connector.jobs.values())
                assert len(jobs) == 1
                task = app.tasks[jobs[0]["task_name"]]
                await task(**jobs[0]["args"])
    finally:
        os.environ.pop("RAG_EVAL_VARIANT", None)

    started_events = [e for e in captured if e.get("event") == "rag_eval_run_started"]
    assert started_events, "rag_eval_run_started log event not emitted"
    assert started_events[0]["variant"] == "contextual_v1"


# ---------------------------------------------------------------------------
# Test 3 - default variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nightly_task_default_variant_baseline():
    """REQ-6 default: when RAG_EVAL_VARIANT is unset, variant must be 'baseline'."""
    import structlog.testing

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    os.environ.pop("RAG_EVAL_VARIANT", None)

    app, connector = _make_app()
    register_eval_tasks(app)

    from knowledge_ingest.eval.suite_loader import Suite

    mock_suite = Suite(name="chat", description="", queries=[])

    with structlog.testing.capture_logs() as captured:
        with patch("knowledge_ingest.eval.suite_loader.load_suite", return_value=mock_suite):
            await app.tasks[_TASK_NAME].defer_async(suite="chat")
            jobs = list(connector.jobs.values())
            assert len(jobs) == 1
            task = app.tasks[jobs[0]["task_name"]]
            await task(**jobs[0]["args"])

    started_events = [e for e in captured if e.get("event") == "rag_eval_run_started"]
    assert started_events, "rag_eval_run_started log event not emitted"
    assert started_events[0]["variant"] == "baseline"


# ---------------------------------------------------------------------------
# Test 4 - queueing lock prevents parallel runs per suite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queueing_lock_prevents_parallel_runs_per_suite():
    """REQ-5: deferring evaluate_retrieval_quality_nightly twice for the same
    suite (without consuming the first job) must raise AlreadyEnqueued.
    """
    import procrastinate.exceptions

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    app, _connector = _make_app()
    register_eval_tasks(app)

    await app.tasks[_TASK_NAME].configure(queueing_lock="rag-eval-chat").defer_async(suite="chat")

    with pytest.raises(procrastinate.exceptions.AlreadyEnqueued):
        await (
            app.tasks[_TASK_NAME].configure(queueing_lock="rag-eval-chat").defer_async(suite="chat")
        )


# ---------------------------------------------------------------------------
# Test 5 - both log events emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_events_emitted():
    """Task body must emit both rag_eval_run_started and rag_eval_run_completed
    with matching suite, variant, and duration_ms keys.
    """
    import structlog.testing

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    os.environ.pop("RAG_EVAL_VARIANT", None)

    app, connector = _make_app()
    register_eval_tasks(app)

    from knowledge_ingest.eval.suite_loader import Suite

    mock_suite = Suite(name="knowledge_org", description="", queries=[])

    with structlog.testing.capture_logs() as captured:
        with patch("knowledge_ingest.eval.suite_loader.load_suite", return_value=mock_suite):
            await app.tasks[_TASK_NAME].defer_async(suite="knowledge_org")
            jobs = list(connector.jobs.values())
            task = app.tasks[jobs[0]["task_name"]]
            await task(**jobs[0]["args"])

    event_names = [e.get("event") for e in captured]
    assert "rag_eval_run_started" in event_names
    assert "rag_eval_run_completed" in event_names

    started = next(e for e in captured if e.get("event") == "rag_eval_run_started")
    completed = next(e for e in captured if e.get("event") == "rag_eval_run_completed")

    assert started["suite"] == "knowledge_org"
    assert completed["suite"] == "knowledge_org"
    assert "variant" in started
    assert "variant" in completed
    assert "duration_ms" in completed


# ---------------------------------------------------------------------------
# Test 6 - queue in LLM lane
# ---------------------------------------------------------------------------


def test_queue_in_llm_lane():
    """RAG_EVAL must be in LLM_QUEUES and ALL_QUEUES (judge LLM is LLM-bound)."""
    from knowledge_ingest import queues

    assert hasattr(queues, "RAG_EVAL"), "queues.RAG_EVAL constant not declared"
    assert queues.RAG_EVAL == "rag-eval"
    assert queues.RAG_EVAL in queues.LLM_QUEUES
    assert queues.RAG_EVAL in queues.ALL_QUEUES


# ---------------------------------------------------------------------------
# Test 7 - expected_chunks matcher avoids generic body false positives
# ---------------------------------------------------------------------------


def test_expected_chunks_canary_does_not_match_generic_body_text():
    """Short generic markers must match strong fields, not arbitrary body text."""
    from knowledge_ingest.eval.ragas_runner import _expected_chunk_canary

    canary = _expected_chunk_canary(
        expected_chunks=["pipedrive"],
        chunks=[
            {
                "chunk_id": "other",
                "title": "CRM overview",
                "text": "This body happens to mention pipedrive as one of many tools.",
            }
        ],
    )

    assert canary == {
        "expected_chunks": ["pipedrive"],
        "matched_chunks": [],
        "missing_chunks": ["pipedrive"],
        "passed": False,
    }


def test_expected_chunks_canary_matches_specific_body_phrase():
    """Long phrase markers may still match body text when no stable title exists."""
    from knowledge_ingest.eval.ragas_runner import _expected_chunk_canary

    canary = _expected_chunk_canary(
        expected_chunks=["specifieke procedure voor uitportering"],
        chunks=[
            {
                "chunk_id": "c1",
                "title": "Generic title",
                "text": "De specifieke procedure voor uitportering staat hier beschreven.",
            }
        ],
    )

    assert canary is not None
    assert canary["matched_chunks"] == ["specifieke procedure voor uitportering"]
    assert canary["missing_chunks"] == []
    assert canary["passed"] is True


# ---------------------------------------------------------------------------
# Test 8 - expected_chunks canary hard-fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expected_chunks_canary_fails_when_missing():
    """A query with expected_chunks must fail before fuzzy RAGAS scoring.

    ``expected_chunks`` are regression canaries: if retrieval no longer returns
    the known-good chunk marker, aggregate RAGAS scores must not hide it.
    """
    from knowledge_ingest.eval.ragas_runner import run_evaluation
    from knowledge_ingest.eval.retrieval_client import RetrievalResult
    from knowledge_ingest.eval.suite_loader import Suite, SuiteQuery

    query = SuiteQuery(
        id="canary-missing",
        query="Waar staat Bubble troubleshoot?",
        org_zitadel_id="org-1",
        expected_topics=["bubble"],
        expected_chunks=["Bubble troubleshoot"],
        kb_slugs=["support", "sip"],
    )
    mock_suite = Suite(name="chat", description="", queries=[query])
    rows: list[dict] = []

    async def _capture_insert(**kwargs):
        rows.append(kwargs)
        return 1

    with (
        patch("knowledge_ingest.eval.suite_loader.load_suite", return_value=mock_suite),
        patch(
            "knowledge_ingest.eval.retrieval_client.retrieve_chunks",
            AsyncMock(
                return_value=RetrievalResult(
                    chunks=[
                        {
                            "chunk_id": "other-chunk",
                            "title": "Other doc",
                            "text": "Unrelated content",
                        }
                    ],
                    retrieval_ms=12,
                    total_tokens=10,
                )
            ),
        ) as mock_retrieve,
        patch("knowledge_ingest.eval.judge_client.generate_answer", AsyncMock()) as mock_answer,
        patch("knowledge_ingest.eval.judge_client.evaluate_query", AsyncMock()) as mock_metrics,
        patch(
            "knowledge_ingest.eval.store.insert_eval_row", AsyncMock(side_effect=_capture_insert)
        ),
    ):
        result = await run_evaluation(suite="chat", variant="baseline")

    assert result == {
        "suite": "chat",
        "variant": "baseline",
        "queries_processed": 1,
        "rows_written": 1,
    }
    assert rows, "expected one eval row to be written"
    row = rows[0]
    assert row["context_precision"] is None
    assert row["context_recall"] is None
    assert row["faithfulness"] is None
    assert row["answer_relevance"] is None
    assert row["retrieved_chunk_ids"] == ["other-chunk"]
    assert row["meta"]["canary"] == {
        "expected_chunks": ["Bubble troubleshoot"],
        "matched_chunks": [],
        "missing_chunks": ["Bubble troubleshoot"],
        "passed": False,
    }
    assert "canary_failed: missing expected_chunks: Bubble troubleshoot" in row["meta"]["errors"]
    mock_answer.assert_not_awaited()
    mock_metrics.assert_not_awaited()
    mock_retrieve.assert_awaited_once_with(
        query="Waar staat Bubble troubleshoot?",
        org_zitadel_id="org-1",
        user_zitadel_id=None,
        kb_slugs=["support", "sip"],
    )


class _PortalResponse:
    def __init__(self, status_code: int, body: dict | list):
        self.status_code = status_code
        self._body = body
        self.request = httpx.Request("GET", "http://portal.test")

    def json(self):
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code),
            )


class _PortalClient:
    def __init__(self, reports: dict[str, dict | int]):
        self.reports = reports
        self.posts: list[str] = []
        self.budgets: list[float] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str, **_kwargs):
        return _PortalResponse(
            200,
            [
                {"org_id": "org-a", "kb_slug": "support"},
                {"org_id": "org-b", "kb_slug": "support"},
            ],
        )

    async def post(self, url: str, **kwargs):
        org_id = "org-a" if "/org-a/" in url else "org-b"
        self.posts.append(org_id)
        self.budgets.append(kwargs["json"]["budget_seconds"])
        result = self.reports[org_id]
        if isinstance(result, int):
            return _PortalResponse(result, {"detail": "must not persist"})
        return _PortalResponse(200, result)


class _QdrantClient:
    async def scroll(self, *, scroll_filter, **_kwargs):
        org_id = next(
            condition.match.value for condition in scroll_filter.must if condition.key == "org_id"
        )
        suffix = org_id[-1]
        return (
            [
                SimpleNamespace(
                    id=f"chunk-{suffix}",
                    payload={
                        "artifact_id": f"artifact-{suffix}",
                        "text": f"private source text {suffix}",
                        "questions": [f"private question {suffix}", "second private question"],
                    },
                )
            ],
            None,
        )


def _assessment(status: str) -> dict:
    result = {
        "question_id": f"question-{status}",
        "source_chunk_id": f"chunk-{status}",
        "source_artifact_id": f"artifact-{status}",
        "source_content_hash": f"content-{status}",
        "present_diagnosis": "covered" if status == "detected_missing" else "incomplete",
        "status": status,
    }
    if status == "detected_missing":
        result["withheld_diagnosis"] = "missing"
    return {
        "snapshot_hash": f"snapshot-{status}",
        "scope_hash": f"scope-{status}",
        "analyzer_version": "support-case-analysis-v10",
        "judge_model": "judge",
        "quality_status": "passed" if status == "detected_missing" else "inconclusive",
        "counts": {status: 1},
        "results": [result],
    }


@pytest.mark.asyncio
async def test_ingest_gap_canary_rotates_and_stores_every_outcome_without_raw_text() -> None:
    from knowledge_ingest.eval import ragas_runner

    rows: list[dict] = []

    async def capture(**kwargs):
        rows.append(kwargs)
        return len(rows)

    first_portal = _PortalClient(
        {"org-a": _assessment("detected_missing"), "org-b": _assessment("unscorable")}
    )
    with (
        patch.object(ragas_runner.httpx, "AsyncClient", return_value=first_portal),
        patch("knowledge_ingest.qdrant_store.get_client", return_value=_QdrantClient()),
        patch("knowledge_ingest.eval.store.insert_eval_row", AsyncMock(side_effect=capture)),
    ):
        report = await ragas_runner.run_ingest_gap_canary(run_day=date(2026, 9, 19), limit=2)

    assert report["attempted"] == 2
    assert report["scored"] == 1
    assert report["unscorable"] == 1
    assert report["quality_status"] == "inconclusive"
    assert all(0 < budget <= 240 for budget in first_portal.budgets)
    assert len(rows) == 3  # two outcomes plus one run summary
    assert all(row["suite"] == "ingest_gap_canary" for row in rows)
    assert all(
        row[metric] is None
        for row in rows
        for metric in ("context_precision", "context_recall", "faithfulness", "answer_relevance")
    )
    stored = str([row["meta"] for row in rows])
    assert "private source text" not in stored
    assert "private question" not in stored

    second_portal = _PortalClient(
        {"org-a": _assessment("detected_missing"), "org-b": _assessment("unscorable")}
    )
    with (
        patch.object(ragas_runner.httpx, "AsyncClient", return_value=second_portal),
        patch("knowledge_ingest.qdrant_store.get_client", return_value=_QdrantClient()),
        patch("knowledge_ingest.eval.store.insert_eval_row", AsyncMock()),
    ):
        await ragas_runner.run_ingest_gap_canary(run_day=date(2026, 9, 20), limit=1)
    assert first_portal.posts[0] != second_portal.posts[0]


@pytest.mark.asyncio
async def test_ingest_gap_canary_persists_partial_rows_before_http_failure() -> None:
    from knowledge_ingest.eval import ragas_runner

    rows: list[dict] = []

    async def capture(**kwargs):
        rows.append(kwargs)
        return len(rows)

    partial = _assessment("detected_missing")
    partial["results"].append(
        {
            "question_id": "question-timeout",
            "source_chunk_id": "chunk-timeout",
            "source_artifact_id": "artifact-timeout",
            "source_content_hash": "content-timeout",
            "status": "failed",
            "error_type": "TimeoutError",
        }
    )
    portal = _PortalClient({"org-a": partial, "org-b": 429})
    with (
        patch.object(ragas_runner.httpx, "AsyncClient", return_value=portal),
        patch("knowledge_ingest.qdrant_store.get_client", return_value=_QdrantClient()),
        patch("knowledge_ingest.eval.store.insert_eval_row", AsyncMock(side_effect=capture)),
        pytest.raises(RuntimeError, match="ingest_gap_canary_failed"),
    ):
        await ragas_runner.run_ingest_gap_canary(run_day=date(2026, 9, 19), limit=4)

    statuses = [row["meta"].get("status") for row in rows]
    assert "detected_missing" in statuses
    assert "failed" in statuses
    assert any(row["meta"].get("error_type") == "TimeoutError" for row in rows)
    assert any(row["meta"].get("http_status") == 429 for row in rows)
    assert "must not persist" not in str([row["meta"] for row in rows])
