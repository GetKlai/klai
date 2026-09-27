"""Unit tests for ``zombie_recovery.recover_zombie_jobs``.

SPEC-PROCRASTINATE-ZOMBIE-001 REQ-1..REQ-5.

The tests mock the procrastinate ``proc_app`` because the recovery logic
is all about correct sequencing + correct calls. Integration with the
real procrastinate schema is covered by the production deploy itself:
the existing 21 zombies are the live regression test.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from procrastinate.exceptions import UniqueViolation

from knowledge_ingest.zombie_recovery import (
    QUEUEING_LOCK_UNIQUE_CONSTRAINT,
    STALLED_WORKER_TIMEOUT_SECONDS,
    recover_zombie_jobs,
    register_zombie_recovery_task,
)


class _FakeApp:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, dict]] = []
        self.periodics: list[tuple[object, dict]] = []

    def task(self, **kwargs):
        def decorator(fn):
            self.tasks.append((fn, kwargs))
            return fn

        return decorator

    def periodic(self, **kwargs):
        def decorator(fn):
            self.periodics.append((fn, kwargs))
            return fn

        return decorator


def _make_proc_app(stalled_jobs: list[SimpleNamespace]) -> MagicMock:
    """Build a proc_app whose connector/job_manager return canned data."""
    proc_app = MagicMock()
    proc_app.job_manager = MagicMock()
    proc_app.job_manager.get_stalled_jobs = AsyncMock(return_value=stalled_jobs)
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(return_value=None)
    proc_app.job_manager.finish_job_by_id_async = AsyncMock(return_value=None)
    return proc_app


@pytest.mark.asyncio
async def test_recovery_clean_when_no_zombies():
    """REQ-3: when no orphan jobs exist, recovery is a no-op."""
    proc_app = _make_proc_app([])

    result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 0}
    proc_app.job_manager.get_stalled_jobs.assert_awaited_once_with(
        seconds_since_heartbeat=STALLED_WORKER_TIMEOUT_SECONDS
    )
    proc_app.job_manager.retry_job_by_id_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_retries_each_orphan_job():
    """REQ-3: every orphan row gets retried with retry_at."""
    jobs = [
        SimpleNamespace(id=100, queue="graphiti-bulk", task_name="ingest_graphiti_episode"),
        SimpleNamespace(id=101, queue="enrich-bulk", task_name="enrich_document_bulk"),
        SimpleNamespace(id=102, queue="graphiti-bulk", task_name="ingest_graphiti_episode"),
    ]
    proc_app = _make_proc_app(jobs)

    result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 3}
    assert proc_app.job_manager.retry_job_by_id_async.await_count == 3
    retried_ids = {
        call.kwargs["job_id"] for call in proc_app.job_manager.retry_job_by_id_async.await_args_list
    }
    assert retried_ids == {100, 101, 102}


@pytest.mark.asyncio
async def test_recovery_continues_when_one_retry_fails():
    """REQ-4: a failing retry on one job does not abort the others."""
    jobs = [
        SimpleNamespace(id=200, queue="graphiti-bulk", task_name="ingest_graphiti_episode"),
        SimpleNamespace(id=201, queue="graphiti-bulk", task_name="ingest_graphiti_episode"),
        SimpleNamespace(id=202, queue="graphiti-bulk", task_name="ingest_graphiti_episode"),
    ]
    proc_app = _make_proc_app(jobs)
    # Middle job raises; outer caller should still see counts for the successes.
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=[None, RuntimeError("simulated DB blip"), None]
    )

    result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 2}
    assert proc_app.job_manager.retry_job_by_id_async.await_count == 3


@pytest.mark.asyncio
async def test_unique_violation_on_queueing_lock_finishes_zombie_and_logs_info():
    """A newer job already queued under the zombie's lock covers the work:
    finish the zombie instead of retrying it, and log once at info, not error.
    """
    job = SimpleNamespace(id=300, queue="graphiti-bulk", task_name="ingest_graphiti_episode")
    proc_app = _make_proc_app([job])
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=UniqueViolation(
            constraint_name=QUEUEING_LOCK_UNIQUE_CONSTRAINT,
            queueing_lock="graphiti:artifact-300",
        )
    )

    with structlog.testing.capture_logs() as captured:
        result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 0}
    proc_app.job_manager.finish_job_by_id_async.assert_awaited_once()
    assert proc_app.job_manager.finish_job_by_id_async.await_args.kwargs["job_id"] == 300
    assert proc_app.job_manager.finish_job_by_id_async.await_args.kwargs["delete_job"] is False

    assert [e for e in captured if e.get("event") == "procrastinate_zombie_retry_failed"] == []
    info_events = [e for e in captured if e.get("event") == "zombie_superseded_by_queued_job"]
    assert len(info_events) == 1
    event = info_events[0]
    assert event["log_level"] == "info"
    assert event["job_id"] == 300
    assert event["queueing_lock"] == "graphiti:artifact-300"


@pytest.mark.asyncio
async def test_second_recovery_pass_does_not_see_superseded_zombie_again():
    """Once finished, the zombie leaves ``doing`` and get_stalled_jobs stops
    returning it on the next minute-level pass.
    """
    job = SimpleNamespace(id=301, queue="graphiti-bulk", task_name="ingest_graphiti_episode")
    proc_app = _make_proc_app([job])
    proc_app.job_manager.get_stalled_jobs = AsyncMock(side_effect=[[job], []])
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=UniqueViolation(
            constraint_name=QUEUEING_LOCK_UNIQUE_CONSTRAINT,
            queueing_lock="graphiti:artifact-301",
        )
    )

    first = await recover_zombie_jobs(proc_app)
    second = await recover_zombie_jobs(proc_app)

    assert first == {"jobs_retried": 0}
    assert second == {"jobs_retried": 0}
    proc_app.job_manager.finish_job_by_id_async.assert_awaited_once()
    assert proc_app.job_manager.get_stalled_jobs.await_count == 2


@pytest.mark.asyncio
async def test_unique_violation_on_other_constraint_still_logs_error():
    """A UniqueViolation NOT on the queueing lock is not a superseded zombie:
    keep today's error-logging behaviour instead of finishing the job.
    """
    job = SimpleNamespace(id=302, queue="graphiti-bulk", task_name="ingest_graphiti_episode")
    proc_app = _make_proc_app([job])
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=UniqueViolation(constraint_name="some_other_constraint", queueing_lock=None)
    )

    with structlog.testing.capture_logs() as captured:
        result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 0}
    proc_app.job_manager.finish_job_by_id_async.assert_not_awaited()
    error_events = [e for e in captured if e.get("event") == "procrastinate_zombie_retry_failed"]
    assert len(error_events) == 1


@pytest.mark.asyncio
async def test_uses_120_second_stalled_worker_timeout():
    """REQ-2: 120s window prevents pruning the live worker about to start."""
    proc_app = _make_proc_app([])

    await recover_zombie_jobs(proc_app)

    proc_app.job_manager.get_stalled_jobs.assert_awaited_once_with(seconds_since_heartbeat=120.0)


def test_periodic_recovery_uses_dedicated_queue_and_lock() -> None:
    app = _FakeApp()

    register_zombie_recovery_task(app)

    assert app.periodics[0][1] == {
        "cron": "* * * * *",
        "periodic_id": "stalled-job-recovery",
    }
    task_config = app.tasks[0][1]
    assert task_config["queue"] == "maintenance"
    assert task_config["queueing_lock"] == "stalled-job-recovery"
    assert hasattr(app, "recover_stalled_jobs_periodic")


@pytest.mark.asyncio
async def test_failed_finish_of_superseded_zombie_does_not_stop_the_pass():
    """A finish that raises is logged and the remaining zombies are still retried."""
    superseded = SimpleNamespace(id=500, queue="graphiti-bulk", task_name="ingest_graphiti_episode")
    other = SimpleNamespace(id=501, queue="enrich-bulk", task_name="enrich_document_bulk")
    proc_app = _make_proc_app([superseded, other])
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=[
            UniqueViolation(
                constraint_name=QUEUEING_LOCK_UNIQUE_CONSTRAINT, queueing_lock="graphiti:a-500"
            ),
            None,
        ]
    )
    proc_app.job_manager.finish_job_by_id_async = AsyncMock(side_effect=RuntimeError("db gone"))

    with structlog.testing.capture_logs() as captured:
        result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 1}
    assert proc_app.job_manager.retry_job_by_id_async.await_count == 2
    failed = [e for e in captured if e.get("event") == "procrastinate_zombie_finish_failed"]
    assert [e["job_id"] for e in failed] == [500]
