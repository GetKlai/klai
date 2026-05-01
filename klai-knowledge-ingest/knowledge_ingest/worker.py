"""Procrastinate worker lifecycle for knowledge-ingest.

Encapsulates everything the FastAPI lifespan needs to do to run the
async task worker correctly:

* Build the libpq-style DSN from ``settings.postgres_dsn`` (procrastinate
  uses psycopg3 / libpq, not asyncpg, and base64 passwords with
  ``/+=`` chars trip stdlib urlparse + libpq key=value parsing).
* Initialise the procrastinate App with all task registrations
  (enrichment, crawl, taxonomy, clustering, ingest, connector-purge).
* Open the connection pool.
* Run zombie recovery (SPEC-PROCRASTINATE-ZOMBIE-001) before starting the
  worker so jobs orphaned by a previous container kill get retried.
* Start the worker subscribed to ``queues.ALL_QUEUES`` (single source
  of truth — SPEC-INGEST-QUEUE-SEPARATION-001).
* On shutdown: cancel the worker task and close the connection pool.

Why a class instead of a free async function:

The previous incarnation lived inline in ``app.py`` lifespan (~60 lines
of lazy imports, DSN rewriting, try/except for zombie recovery, worker
task management). Each cross-cutting concern around the worker (queue
add, recovery improvement, shutdown semantics) had to be retrofitted
into that block. Three SPECs in one week (CONNECTOR-DELETE-LIFECYCLE-001
PR #253, PROCRASTINATE-ZOMBIE-001, INGEST-QUEUE-SEPARATION-001) all
landed there with growing comment overhead. Centralising into one class
gives every future SPEC a single, tested seam to plug into.

Usage::

    async with WorkerLifecycle.start(postgres_dsn=settings.postgres_dsn):
        # worker is running; FastAPI app handles requests
        yield
    # worker is gracefully shut down here
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

logger = structlog.get_logger()


def _build_libpq_dsn(postgres_dsn: str) -> str:
    """Convert a SQLAlchemy ``postgresql+asyncpg://...`` URL to a libpq
    ``key='value'`` connection string for psycopg3.

    Wraps the password in single quotes because base64-encoded passwords
    routinely end with ``=`` which libpq's key=value format interprets
    as a new separator. The ``\\`` and ``'`` chars inside the password
    are escaped per libpq docs.
    """
    from sqlalchemy.engine import make_url

    u = make_url(postgres_dsn)
    pw = (u.password or "").replace("\\", "\\\\").replace("'", "\\'")
    return (
        f"host={u.host} port={u.port or 5432} dbname={u.database} user={u.username} password='{pw}'"
    )


class WorkerLifecycle:
    """Bootstrap, run, and shut down the procrastinate worker.

    Use ``WorkerLifecycle.start(postgres_dsn=...)`` as an async context
    manager. The worker runs for the duration of the ``async with`` block
    and is gracefully cancelled on exit.
    """

    # SPEC-WORKER-LANES-001 — concurrency tuning per lane.
    # I/O lane: HTTP-bound, can fan out parallel without rate concerns.
    # LLM lane: bounded by Mistral upstream + token-bucket in graph.py
    # (1 req/s for graphiti). 4 in-flight LLM jobs is the empirically
    # validated upper bound that keeps the rate limiter happy without
    # leaving slots idle.
    IO_CONCURRENCY: int = 8
    LLM_CONCURRENCY: int = 4

    def __init__(self, *, postgres_dsn: str) -> None:
        self.postgres_dsn = postgres_dsn
        self.proc_app: Any | None = None
        # SPEC-WORKER-LANES-001: two procrastinate workers, one per lane.
        # Both share the same App + connector pool, but subscribe to disjoint
        # queue sets and have independent concurrency semaphores. This is the
        # only way to give I/O work latency guarantees while LLM work runs at
        # its natural throughput — procrastinate has no per-queue fairness
        # within a single worker.
        self._io_worker_task: asyncio.Task | None = None
        self._llm_worker_task: asyncio.Task | None = None
        self._stack = AsyncExitStack()

    @classmethod
    @asynccontextmanager
    async def start(cls, *, postgres_dsn: str) -> AsyncIterator[WorkerLifecycle]:
        """Async context manager that yields a running worker."""
        instance = cls(postgres_dsn=postgres_dsn)
        async with instance:
            yield instance

    async def __aenter__(self) -> WorkerLifecycle:
        # Lazy imports: procrastinate pulls in psycopg/libpq which is not
        # installed in test environments where ENRICHMENT_ENABLED=false.
        # Callers should gate WorkerLifecycle creation on enrichment_enabled.
        import procrastinate

        from knowledge_ingest import enrichment_tasks

        conninfo = _build_libpq_dsn(self.postgres_dsn)
        # kwargs={} works around psycopg-pool 3.x: default kwargs=None
        # leads to **None TypeError when the pool builds connection params.
        connector = procrastinate.PsycopgConnector(conninfo=conninfo, kwargs={})
        self.proc_app = enrichment_tasks.init_app(connector)
        logger.info("procrastinate_app_initialised")

        await self._stack.enter_async_context(self.proc_app.open_async())

        # SPEC-PROCRASTINATE-ZOMBIE-001: retry jobs orphaned by a previous
        # container kill BEFORE starting the new worker. Best-effort: a
        # recovery failure must not block worker startup.
        try:
            from knowledge_ingest.zombie_recovery import recover_zombie_jobs

            await recover_zombie_jobs(self.proc_app)
        except Exception:
            logger.exception("procrastinate_zombie_recovery_failed")

        # SPEC-WORKER-LANES-001: start two workers in parallel, each
        # subscribed to one lane only. This is what gives I/O work latency
        # independence from LLM work — no FIFO across lanes, no concurrency
        # competition. A single worker subscribed to ALL_QUEUES (the previous
        # design) would still let a backlog of LLM jobs starve I/O work
        # because procrastinate fetches the oldest todo across the worker's
        # queue set, regardless of queue identity.
        from knowledge_ingest.queues import IO_QUEUES, LLM_QUEUES

        self._io_worker_task = asyncio.create_task(
            self.proc_app.run_worker_async(
                queues=IO_QUEUES,
                concurrency=self.IO_CONCURRENCY,
                install_signal_handlers=False,
            ),
            name="procrastinate-worker-io",
        )
        self._llm_worker_task = asyncio.create_task(
            self.proc_app.run_worker_async(
                queues=LLM_QUEUES,
                concurrency=self.LLM_CONCURRENCY,
                install_signal_handlers=False,
            ),
            name="procrastinate-worker-llm",
        )
        logger.info(
            "procrastinate_workers_started",
            io_queues=IO_QUEUES,
            io_concurrency=self.IO_CONCURRENCY,
            llm_queues=LLM_QUEUES,
            llm_concurrency=self.LLM_CONCURRENCY,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Cancel both lane workers in parallel and wait for them to exit.
        worker_tasks = [t for t in (self._io_worker_task, self._llm_worker_task) if t is not None]
        if worker_tasks:
            logger.info("procrastinate_workers_stopping", count=len(worker_tasks))
            for t in worker_tasks:
                t.cancel()
            results = await asyncio.gather(*worker_tasks, return_exceptions=True)
            for task, result in zip(worker_tasks, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    # Expected: cancellation is how we ask each worker to stop.
                    continue
                if isinstance(result, BaseException):
                    # Unexpected. Log with traceback but do not re-raise —
                    # shutdown must continue so db.close_pool() runs and the
                    # container exits cleanly.
                    logger.exception(
                        "procrastinate_worker_shutdown_error",
                        worker=task.get_name(),
                        exc_info=(type(result), result, result.__traceback__),
                    )
        await self._stack.aclose()
        logger.info("procrastinate_workers_stopped")
