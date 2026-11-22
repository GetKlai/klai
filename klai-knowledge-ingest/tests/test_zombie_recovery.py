"""Unit tests for ``zombie_recovery.recover_zombie_jobs``.

SPEC-PROCRASTINATE-ZOMBIE-001 REQ-1..REQ-5.

The tests mock the procrastinate ``proc_app`` because the recovery logic
is all about correct sequencing + correct calls. The same paths against the
real queue tables are in tests/integration/test_document_jobs_in_queue_postgres.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from knowledge_ingest.zombie_recovery import (
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


def _make_proc_app(
    stalled_jobs: list[SimpleNamespace], twins: tuple[SimpleNamespace, ...] = ()
) -> MagicMock:
    """Build a proc_app whose job_manager returns canned data.

    ``twins`` are the other jobs in the queue, listed by their queueing lock.
    """
    proc_app = MagicMock()
    proc_app.job_manager = MagicMock()
    proc_app.job_manager.get_stalled_jobs = AsyncMock(return_value=stalled_jobs)
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(return_value=None)
    proc_app.job_manager.finish_job_by_id_async = AsyncMock(return_value=None)
    proc_app.job_manager.list_jobs_async = AsyncMock(
        side_effect=lambda queueing_lock: [
            job for job in [*stalled_jobs, *twins] if job.queueing_lock == queueing_lock
        ]
    )
    return proc_app


def _job(job_id: int, queue: str, task_name: str, **task_kwargs) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        queue=queue,
        task_name=task_name,
        task_kwargs=task_kwargs or {"artifact_id": f"artifact-{job_id}"},
        queueing_lock=f"lock-{job_id}",
        status="doing",
    )


def _twin(job: SimpleNamespace, status: str = "todo") -> SimpleNamespace:
    """Another job holding ``job``'s queueing lock for the same work."""
    return SimpleNamespace(
        id=-job.id,
        task_name=job.task_name,
        task_kwargs=dict(job.task_kwargs),
        queueing_lock=job.queueing_lock,
        status=status,
    )


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
        _job(100, "graphiti-bulk", "ingest_graphiti_episode"),
        _job(101, "enrich-bulk", "enrich_document_bulk"),
        _job(102, "graphiti-bulk", "ingest_graphiti_episode"),
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
        _job(200, "graphiti-bulk", "ingest_graphiti_episode"),
        _job(201, "graphiti-bulk", "ingest_graphiti_episode"),
        _job(202, "graphiti-bulk", "ingest_graphiti_episode"),
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
async def test_a_queued_job_doing_the_same_work_on_the_lock_finishes_the_zombie_aborted():
    """A newer job already queued under the zombie's lock covers the work:
    finish the zombie instead of retrying it, and log once at info, not error.
    """
    job = _job(300, "graphiti-bulk", "ingest_graphiti_episode")
    proc_app = _make_proc_app([job], twins=(_twin(job),))

    with structlog.testing.capture_logs() as captured:
        result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 0}
    proc_app.job_manager.retry_job_by_id_async.assert_not_awaited()
    proc_app.job_manager.finish_job_by_id_async.assert_awaited_once()
    assert proc_app.job_manager.finish_job_by_id_async.await_args.kwargs["job_id"] == 300
    assert proc_app.job_manager.finish_job_by_id_async.await_args.kwargs["status"].value == (
        "aborted"
    )
    assert proc_app.job_manager.finish_job_by_id_async.await_args.kwargs["delete_job"] is False

    assert [e for e in captured if e.get("event") == "procrastinate_zombie_retry_failed"] == []
    info_events = [e for e in captured if e.get("event") == "zombie_superseded_by_queued_job"]
    assert len(info_events) == 1
    event = info_events[0]
    assert event["log_level"] == "info"
    assert event["job_id"] == 300
    assert event["queueing_lock"] == "lock-300"


@pytest.mark.asyncio
async def test_second_recovery_pass_does_not_see_superseded_zombie_again():
    """Once finished, the zombie leaves ``doing`` and get_stalled_jobs stops
    returning it on the next minute-level pass.
    """
    job = _job(301, "graphiti-bulk", "ingest_graphiti_episode")
    proc_app = _make_proc_app([job], twins=(_twin(job),))
    proc_app.job_manager.get_stalled_jobs = AsyncMock(side_effect=[[job], []])

    first = await recover_zombie_jobs(proc_app)
    second = await recover_zombie_jobs(proc_app)

    assert first == {"jobs_retried": 0}
    assert second == {"jobs_retried": 0}
    proc_app.job_manager.finish_job_by_id_async.assert_awaited_once()
    assert proc_app.job_manager.get_stalled_jobs.await_count == 2


@pytest.mark.asyncio
async def test_a_running_job_on_the_queueing_lock_does_not_stop_the_retry():
    """Only a todo job holds the queueing lock: a running twin leaves the
    zombie free to go back to todo, so it is retried, not finished.
    """
    job = _job(302, "graphiti-bulk", "ingest_graphiti_episode")
    proc_app = _make_proc_app([job], twins=(_twin(job, status="doing"),))

    result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 1}
    proc_app.job_manager.finish_job_by_id_async.assert_not_awaited()


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
    superseded = _job(500, "graphiti-bulk", "ingest_graphiti_episode")
    other = _job(501, "enrich-bulk", "enrich_document_bulk")
    proc_app = _make_proc_app([superseded, other], twins=(_twin(superseded),))
    proc_app.job_manager.finish_job_by_id_async = AsyncMock(side_effect=RuntimeError("db gone"))

    with structlog.testing.capture_logs() as captured:
        result = await recover_zombie_jobs(proc_app)

    assert result == {"jobs_retried": 1}
    proc_app.job_manager.retry_job_by_id_async.assert_awaited_once()
    assert proc_app.job_manager.retry_job_by_id_async.await_args.kwargs["job_id"] == 501
    failed = [e for e in captured if e.get("event") == "procrastinate_zombie_finish_failed"]
    assert [e["job_id"] for e in failed] == [500]
