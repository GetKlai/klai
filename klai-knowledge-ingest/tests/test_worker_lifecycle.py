"""Unit tests for ``WorkerLifecycle``.

SPEC-WORKER-LANES-001 REQ-1 + REQ-2. Verify that the lifecycle starts
two procrastinate workers — one for the I/O lane, one for the LLM lane —
with the right queues + concurrency for each, and that both shut down
cleanly on context exit.

Approach: stub procrastinate + enrichment_tasks via ``sys.modules``
injection so the lifecycle's lazy imports resolve to mocks. We do NOT
need a real procrastinate connection — the test only checks what
``run_worker_async`` was called with.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import queues


@pytest.fixture
def stub_procrastinate(monkeypatch):
    """Replace procrastinate + enrichment_tasks with stubs.

    The lifecycle's `__aenter__` does:
      * ``import procrastinate`` → ``PsycopgConnector`` constructor
      * ``from knowledge_ingest import enrichment_tasks`` → ``init_app``
      * ``proc_app.open_async()`` → async context manager
      * ``proc_app.run_worker_async(...)`` → coroutine that runs forever
    Each is mocked so we can observe the calls.
    """
    # CPython's ``from package import submodule`` resolves to
    # ``getattr(package, submodule)`` after the import, which means a bare
    # ``monkeypatch.setitem(sys.modules, ...)`` is NOT sufficient -- the
    # parent package attribute still points at the original module. Patch
    # both the inner symbols (so any reference style picks up the stub)
    # and use ``monkeypatch.setattr`` on the package attribute as a
    # defence-in-depth fallback.

    # 1) Replace ``procrastinate.PsycopgConnector`` -- WorkerLifecycle
    #    constructs the connector with this name.
    proc_stub = types.ModuleType("procrastinate")
    proc_stub.PsycopgConnector = MagicMock(return_value="connector-sentinel")
    monkeypatch.setitem(sys.modules, "procrastinate", proc_stub)

    @asynccontextmanager
    async def _open_async():
        yield

    # Build a proc_app whose run_worker_async is observable.
    proc_app = MagicMock()
    proc_app.open_async = MagicMock(return_value=_open_async())
    # Each worker call returns a never-completing coroutine so the lifecycle
    # treats it as "still running" until cancelled.
    run_worker_calls: list[dict] = []

    async def _never_complete(**kwargs):
        run_worker_calls.append(kwargs)
        await asyncio.Event().wait()  # blocks forever; cancellable

    proc_app.run_worker_async = _never_complete

    class _Worker:
        """The lifecycle builds the procrastinate Worker itself; its run()
        delegates to ``proc_app.run_worker_async`` so a test can swap the body."""

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.worker_id = None
            self._stop_event = asyncio.Event()

        async def run(self):
            self.worker_id = 1
            try:
                await proc_app.run_worker_async(**self.kwargs)
            finally:
                self.worker_id = None

    proc_app._worker = _Worker

    # 2) Stub ``init_app`` on the *real* enrichment_tasks module so
    #    ``from knowledge_ingest import enrichment_tasks`` returns the
    #    actual module (with its real symbol table) but the function
    #    we care about is mocked.
    init_app_mock = MagicMock(return_value=proc_app)
    monkeypatch.setattr(
        "knowledge_ingest.enrichment_tasks.init_app",
        init_app_mock,
    )

    # 3) Same trick for zombie_recovery.recover_zombie_jobs. The
    #    lifecycle imports it lazily inside ``__aenter__``.
    recover_mock = AsyncMock(return_value={"workers_pruned": 0, "jobs_retried": 0})
    monkeypatch.setattr(
        "knowledge_ingest.zombie_recovery.recover_zombie_jobs",
        recover_mock,
    )

    return {
        "proc_app": proc_app,
        "run_worker_calls": run_worker_calls,
        "init_app": init_app_mock,
        "recover_zombie_jobs": recover_mock,
    }


@pytest.mark.asyncio
async def test_lifecycle_starts_four_workers(stub_procrastinate):
    """The three work lanes plus an unstarvable maintenance lane start."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        # Yield control once so background asyncio.create_task instances
        # actually start running and call run_worker_async.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    assert len(calls) == 4, (
        f"expected 4 worker calls (I/O + interactive + LLM + maintenance), got {len(calls)}"
    )


@pytest.mark.asyncio
async def test_all_workers_bound_shutdown_and_share_stalled_timeout(stub_procrastinate):
    """A deploy must abort/requeue work before Compose's 90-second hard kill."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    assert len(calls) == 4
    assert {call["shutdown_graceful_timeout"] for call in calls} == {
        WorkerLifecycle.SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS
    }
    assert {call["stalled_worker_timeout"] for call in calls} == {
        WorkerLifecycle.STALLED_WORKER_TIMEOUT_SECONDS
    }


@pytest.mark.asyncio
async def test_maintenance_worker_has_dedicated_single_slot(stub_procrastinate):
    """Zombie recovery cannot queue behind eight long-running crawls."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    call = next(
        item
        for item in stub_procrastinate["run_worker_calls"]
        if tuple(item["queues"]) == tuple(queues.MAINTENANCE_QUEUES)
    )
    assert call["concurrency"] == 1


@pytest.mark.asyncio
async def test_io_worker_subscribed_to_io_queues_only(stub_procrastinate):
    """REQ-1: I/O worker MUST NOT subscribe to LLM queues."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    queue_sets = [tuple(c["queues"]) for c in calls]
    assert tuple(queues.IO_QUEUES) in queue_sets
    # The IO call's queues must not include any LLM queue.
    io_call = next(c for c in calls if tuple(c["queues"]) == tuple(queues.IO_QUEUES))
    assert set(io_call["queues"]).isdisjoint(queues.LLM_QUEUES)
    assert set(io_call["queues"]).isdisjoint(queues.INTERACTIVE_QUEUES)


@pytest.mark.asyncio
async def test_llm_worker_subscribed_to_llm_queues_only(stub_procrastinate):
    """REQ-1: LLM worker MUST NOT subscribe to I/O queues."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    queue_sets = [tuple(c["queues"]) for c in calls]
    assert tuple(queues.LLM_QUEUES) in queue_sets
    llm_call = next(c for c in calls if tuple(c["queues"]) == tuple(queues.LLM_QUEUES))
    assert set(llm_call["queues"]).isdisjoint(queues.IO_QUEUES)
    assert set(llm_call["queues"]).isdisjoint(queues.INTERACTIVE_QUEUES)


@pytest.mark.asyncio
async def test_interactive_worker_subscribed_to_interactive_queue_only(stub_procrastinate):
    """Interactive lane: user-triggered re-syncs must not share a worker with
    bulk queues, or a crawl backlog starves them (intermedia.com incident,
    2026-08-14: a re-sync sat behind ~550 bulk jobs)."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    queue_sets = [tuple(c["queues"]) for c in calls]
    assert tuple(queues.INTERACTIVE_QUEUES) in queue_sets
    interactive_call = next(
        c for c in calls if tuple(c["queues"]) == tuple(queues.INTERACTIVE_QUEUES)
    )
    assert set(interactive_call["queues"]).isdisjoint(queues.IO_QUEUES)
    assert set(interactive_call["queues"]).isdisjoint(queues.LLM_QUEUES)
    assert interactive_call["concurrency"] == WorkerLifecycle.INTERACTIVE_CONCURRENCY


@pytest.mark.asyncio
async def test_lane_concurrency_values(stub_procrastinate):
    """REQ-1: I/O concurrency is higher than LLM (HTTP fan-out vs rate-limited LLM)."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    calls = stub_procrastinate["run_worker_calls"]
    by_queue = {tuple(c["queues"]): c for c in calls}
    io_call = by_queue[tuple(queues.IO_QUEUES)]
    llm_call = by_queue[tuple(queues.LLM_QUEUES)]
    assert io_call["concurrency"] == WorkerLifecycle.IO_CONCURRENCY
    assert llm_call["concurrency"] == WorkerLifecycle.LLM_CONCURRENCY
    assert io_call["concurrency"] >= llm_call["concurrency"], (
        "I/O lane should not have LOWER concurrency than LLM lane — "
        "the whole point of the split is to give I/O latency room"
    )


@pytest.mark.asyncio
async def test_zombie_recovery_runs_before_workers_start(stub_procrastinate):
    """SPEC-PROCRASTINATE-ZOMBIE-001: recover orphan jobs before serving."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    stub_procrastinate["recover_zombie_jobs"].assert_awaited_once()


@pytest.mark.asyncio
async def test_zombie_recovery_failure_does_not_block_workers(stub_procrastinate):
    """SPEC-PROCRASTINATE-ZOMBIE-001 REQ-4: recovery is best-effort."""
    stub_procrastinate["recover_zombie_jobs"].side_effect = RuntimeError("simulated DB blip")

    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Workers still started despite the recovery failure.
    assert len(stub_procrastinate["run_worker_calls"]) == 4


@pytest.mark.asyncio
async def test_procrastinate_pool_is_sized_for_all_lanes(stub_procrastinate):
    """The 4-connection psycopg_pool default starved the side tasks (PoolTimeout)."""
    from knowledge_ingest.worker import WorkerLifecycle

    async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
        pass

    connector_kwargs = sys.modules["procrastinate"].PsycopgConnector.call_args.kwargs
    assert connector_kwargs["min_size"] == WorkerLifecycle.POOL_MIN_SIZE
    assert connector_kwargs["max_size"] == WorkerLifecycle.POOL_MAX_SIZE


@pytest.mark.asyncio
async def test_requested_shutdown_does_not_restart_lanes(stub_procrastinate):
    from structlog.testing import capture_logs

    from knowledge_ingest.worker import WorkerLifecycle

    with capture_logs() as logs:
        async with WorkerLifecycle.start(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d"):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)

    assert len(stub_procrastinate["run_worker_calls"]) == 4
    assert not [e for e in logs if e["event"] == "procrastinate_worker_lane_died"]


@pytest.mark.asyncio
async def test_shutdown_completes_when_worker_cleanup_swallows_the_cancel(
    stub_procrastinate, monkeypatch
):
    """Worker.run() turns a cancel into stop() and awaits its own cleanup; when
    that cleanup raises (unregister_worker while Postgres is down) the
    CancelledError is replaced by an ordinary exception. That must end the
    lane, not restart it, or __aexit__ waits until Docker kills the container."""
    from knowledge_ingest.worker import WorkerLifecycle

    monkeypatch.setattr(WorkerLifecycle, "LANE_RESTART_MIN_BACKOFF_SECONDS", 0.0)
    runs = 0

    async def _run_worker(**kwargs):
        nonlocal runs
        runs += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("unregister_worker failed") from None

    stub_procrastinate["proc_app"].run_worker_async = _run_worker

    lifecycle = WorkerLifecycle(postgres_dsn="postgresql+asyncpg://u:p@h:5432/d")
    await lifecycle.__aenter__()
    await asyncio.sleep(0)
    await asyncio.wait_for(lifecycle.__aexit__(None, None, None), timeout=2)

    assert runs == 4
