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

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_NAME = "knowledge_ingest.eval.ragas_runner.evaluate_retrieval_quality_nightly"


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

    try:
        os.environ["RAG_EVAL_VARIANT"] = "contextual_v1"
        with structlog.testing.capture_logs() as captured:
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

    with structlog.testing.capture_logs() as captured:
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

    with structlog.testing.capture_logs() as captured:
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
