"""Lane supervision against the real procrastinate Worker (verified on 3.10.0).

WorkerLifecycle reads ``Worker.worker_id`` and ``Worker._stop_event`` and
builds the worker through ``App._worker``, none of which is public API. This
test runs a real Worker on procrastinate's InMemoryConnector, so a bump that
renames or changes them turns it red instead of leaving /health wrong.

The production failure it reproduces (22-25 Sep 2026): a side task hit
PoolTimeout, ``_monitor_side_tasks`` called ``stop()`` and the lane stayed
dead until the next deploy.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import queues


@pytest.fixture
def procrastinate(monkeypatch):
    """The real package; conftest installs a MagicMock stub for every other test."""

    def _loaded() -> list[str]:
        return [m for m in sys.modules if m == "procrastinate" or m.startswith("procrastinate.")]

    for name in _loaded():
        monkeypatch.delitem(sys.modules, name)
    try:
        importlib.import_module("psycopg_pool")
    except ImportError:
        # No libpq on this host (macOS dev); InMemoryConnector never uses psycopg.
        for name in ("psycopg", "psycopg.pq", "psycopg.rows", "psycopg.types"):
            monkeypatch.setitem(sys.modules, name, MagicMock())
        monkeypatch.setitem(sys.modules, "psycopg.types.json", MagicMock())
        monkeypatch.setitem(sys.modules, "psycopg_pool", MagicMock())
    yield importlib.import_module("procrastinate")
    for name in _loaded():
        del sys.modules[name]


async def _until(condition, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_lane_stopped_by_failing_side_task_is_dead_then_restarted_and_runs_jobs(
    procrastinate, monkeypatch
):
    from procrastinate.testing import InMemoryConnector

    from knowledge_ingest.worker import WorkerLifecycle

    assert importlib.metadata.version("procrastinate") == "3.10.0"

    connector = InMemoryConnector()
    app = procrastinate.App(
        connector=connector,
        worker_defaults={
            "update_heartbeat_interval": 0.01,
            "fetch_job_polling_interval": 0.01,
            "listen_notify": False,
        },
    )
    release_slow_job = asyncio.Event()
    processed: list[str] = []

    @app.task(name="slow", queue=queues.IO_QUEUES[0])
    async def slow() -> None:
        await release_slow_job.wait()
        processed.append("slow")

    @app.task(name="quick", queue=queues.IO_QUEUES[0])
    async def quick() -> None:
        processed.append("quick")

    # Without a periodic task the deferrer side task returns at once, and
    # _monitor_side_tasks stops watching after the first side task ends.
    @app.periodic(cron="0 0 1 1 *")
    @app.task(name="yearly", queue=queues.MAINTENANCE_QUEUES[0])
    async def yearly(timestamp: int) -> None:
        pass

    heartbeat_fails = False
    update_heartbeat = connector.update_heartbeat_run

    async def _update_heartbeat_run(worker_id: int) -> None:
        if heartbeat_fails:
            raise RuntimeError("couldn't get a connection after 30.00 sec")
        await update_heartbeat(worker_id)

    connector.update_heartbeat_run = _update_heartbeat_run
    monkeypatch.setattr(procrastinate, "PsycopgConnector", lambda **_: connector)
    monkeypatch.setattr("knowledge_ingest.enrichment_tasks.init_app", lambda _connector: app)
    monkeypatch.setattr("knowledge_ingest.zombie_recovery.recover_zombie_jobs", AsyncMock())
    monkeypatch.setattr(WorkerLifecycle, "LANE_RESTART_MIN_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(WorkerLifecycle, "SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS", 5.0)

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d") as w:
        await _until(lambda: not w.dead_lanes)

        await slow.defer_async()
        await _until(lambda: any(j["status"] == "doing" for j in connector.jobs.values()))

        heartbeat_fails = True
        # Dead as soon as procrastinate asks the worker to stop, while it is
        # still waiting for the running job, not once its shutdown finished.
        await _until(lambda: "io" in w.dead_lanes)
        assert processed == []
        assert any(j["status"] == "doing" for j in connector.jobs.values())

        heartbeat_fails = False
        release_slow_job.set()
        await _until(lambda: not w.dead_lanes)

        await quick.defer_async()
        await _until(lambda: "quick" in processed)
