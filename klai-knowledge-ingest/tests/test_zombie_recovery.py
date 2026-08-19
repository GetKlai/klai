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


def _make_proc_app(stalled_jobs: list[SimpleNamespace]) -> MagicMock:
    """Build a proc_app whose connector/job_manager return canned data."""
    proc_app = MagicMock()
    proc_app.job_manager = MagicMock()
    proc_app.job_manager.get_stalled_jobs = AsyncMock(return_value=stalled_jobs)
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(return_value=None)
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
