"""Unit tests for ``zombie_recovery.recover_zombie_jobs``.

SPEC-PROCRASTINATE-ZOMBIE-001 REQ-1..REQ-5.

The tests mock the procrastinate ``proc_app`` because the recovery logic
is all about correct sequencing + correct calls. Integration with the
real procrastinate schema is covered by the production deploy itself:
the existing 21 zombies are the live regression test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest.zombie_recovery import (
    STALLED_WORKER_TIMEOUT_SECONDS,
    recover_zombie_jobs,
)


def _make_proc_app(stalled_workers: list[int], doing_rows: list[dict]) -> MagicMock:
    """Build a proc_app whose connector/job_manager return canned data."""
    proc_app = MagicMock()
    proc_app.job_manager = MagicMock()
    proc_app.job_manager.prune_stalled_workers = AsyncMock(return_value=stalled_workers)
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(return_value=None)
    proc_app.connector = MagicMock()
    proc_app.connector.execute_query_all_async = AsyncMock(return_value=doing_rows)
    return proc_app


@pytest.mark.asyncio
async def test_recovery_clean_when_no_zombies():
    """REQ-3: when no orphan jobs exist, recovery is a no-op."""
    proc_app = _make_proc_app(stalled_workers=[], doing_rows=[])

    result = await recover_zombie_jobs(proc_app)

    assert result == {"workers_pruned": 0, "jobs_retried": 0}
    proc_app.job_manager.prune_stalled_workers.assert_awaited_once_with(
        STALLED_WORKER_TIMEOUT_SECONDS
    )
    proc_app.job_manager.retry_job_by_id_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_retries_each_orphan_job():
    """REQ-3: every orphan row gets retried with retry_at."""
    rows = [
        {"id": 100, "queue_name": "graphiti-bulk", "task_name": "ingest_graphiti_episode"},
        {"id": 101, "queue_name": "enrich-bulk", "task_name": "enrich_document_bulk"},
        {"id": 102, "queue_name": "graphiti-bulk", "task_name": "ingest_graphiti_episode"},
    ]
    proc_app = _make_proc_app(stalled_workers=[42, 43], doing_rows=rows)

    result = await recover_zombie_jobs(proc_app)

    assert result == {"workers_pruned": 2, "jobs_retried": 3}
    assert proc_app.job_manager.retry_job_by_id_async.await_count == 3
    retried_ids = {
        call.kwargs["job_id"] for call in proc_app.job_manager.retry_job_by_id_async.await_args_list
    }
    assert retried_ids == {100, 101, 102}


@pytest.mark.asyncio
async def test_recovery_continues_when_one_retry_fails():
    """REQ-4: a failing retry on one job does not abort the others."""
    rows = [
        {"id": 200, "queue_name": "graphiti-bulk", "task_name": "ingest_graphiti_episode"},
        {"id": 201, "queue_name": "graphiti-bulk", "task_name": "ingest_graphiti_episode"},
        {"id": 202, "queue_name": "graphiti-bulk", "task_name": "ingest_graphiti_episode"},
    ]
    proc_app = _make_proc_app(stalled_workers=[], doing_rows=rows)
    # Middle job raises; outer caller should still see counts for the successes.
    proc_app.job_manager.retry_job_by_id_async = AsyncMock(
        side_effect=[None, RuntimeError("simulated DB blip"), None]
    )

    result = await recover_zombie_jobs(proc_app)

    assert result == {"workers_pruned": 0, "jobs_retried": 2}
    assert proc_app.job_manager.retry_job_by_id_async.await_count == 3


@pytest.mark.asyncio
async def test_select_uses_status_doing_and_worker_id_null():
    """REQ-3: the SQL must filter on status='doing' AND worker_id IS NULL."""
    proc_app = _make_proc_app(stalled_workers=[], doing_rows=[])

    await recover_zombie_jobs(proc_app)

    proc_app.connector.execute_query_all_async.assert_awaited_once()
    call_kwargs = proc_app.connector.execute_query_all_async.await_args.kwargs
    query = call_kwargs["query"]
    assert "status = 'doing'" in query
    assert "worker_id IS NULL" in query


@pytest.mark.asyncio
async def test_uses_120_second_stalled_worker_timeout():
    """REQ-2: 120s window prevents pruning the live worker about to start."""
    proc_app = _make_proc_app(stalled_workers=[], doing_rows=[])

    await recover_zombie_jobs(proc_app)

    proc_app.job_manager.prune_stalled_workers.assert_awaited_once_with(120.0)
