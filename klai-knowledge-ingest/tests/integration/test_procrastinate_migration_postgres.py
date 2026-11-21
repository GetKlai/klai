"""Alembic revision cb73fd8d4df4 takes a procrastinate 3.10 queue to one the installed 3.12 runs on.

The 3.10 schema is generated at test time from procrastinate 3.10.0 in a
throwaway ``uv`` environment, into a schema of its own so the database's own
queue tables are left alone. Needs ``GRAPH_REFRESH_BUDGET_TEST_DSN``, ``uv``
and the real procrastinate (libpq).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

import asyncpg
import pytest

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")
_REVISION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/0017_procrastinate_3_12_pre_deploy.py"
)
# uv comes from PATH, as in CI. The schema is read straight from the installed
# files, because importing procrastinate would need libpq.
_PRINT_SCHEMA_3_10 = [
    "uv",
    "run",
    "--no-project",
    "--with",
    "procrastinate==3.10.0",
    "python",
    "-c",
    "from importlib.metadata import distribution; "
    "print(distribution('procrastinate').locate_file('procrastinate/sql/schema.sql').read_text())",
]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


async def _upgrade(revision, schema: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import pool
    from sqlalchemy.ext.asyncio import create_async_engine

    def _run(connection) -> None:
        context = MigrationContext.configure(connection)
        with Operations.context(context), context.begin_transaction():
            revision.upgrade()

    assert _DSN is not None
    engine = create_async_engine(
        _DSN.replace("postgresql://", "postgresql+asyncpg://", 1),
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


async def _round_trip(procrastinate, schema: str) -> int:
    """Defer, fetch, retry, fetch again and finish one job; return its id."""
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=_DSN, kwargs={"options": f"-c search_path={schema}"}
        )
    )

    @app.task(name="migration_probe")
    def probe() -> None: ...

    async with app.open_async():
        manager = app.job_manager
        worker_id = await manager.register_worker()
        job_id = await probe.configure(
            lock=f"lock-{uuid.uuid4().hex}", queueing_lock=f"q-{uuid.uuid4().hex}"
        ).defer_async()
        job = await manager.fetch_job(queues=None, worker_id=worker_id)
        assert job is not None
        assert job.id == job_id
        await manager.retry_job(job)
        job = await manager.fetch_job(queues=None, worker_id=worker_id)
        assert job is not None
        assert (job.id, job.attempts) == (job_id, 1)
        await manager.finish_job(job, status=procrastinate.jobs.Status.SUCCEEDED, delete_job=False)
        await manager.unregister_worker(worker_id)
    return job_id


async def test_a_3_10_queue_runs_3_12_jobs_after_the_revision_and_after_running_it_again(
    real_procrastinate,
) -> None:
    if not real_procrastinate.has_libpq:
        message = "the psycopg connector needs libpq"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    schema_3_10 = subprocess.run(  # noqa: S603 - fixed argv, no input from outside the test
        _PRINT_SCHEMA_3_10,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    spec = importlib.util.spec_from_file_location("procrastinate_3_12_revision", _REVISION)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    schema = f"queue_3_10_{uuid.uuid4().hex[:12]}"
    assert _DSN is not None
    conn = await asyncpg.connect(_DSN, server_settings={"search_path": schema})
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(schema_3_10)

        await _upgrade(revision, schema)
        first = await _round_trip(real_procrastinate, schema)
        await _upgrade(revision, schema)
        second = await _round_trip(real_procrastinate, schema)

        rows = await conn.fetch(
            "SELECT id, status::text AS status, attempts FROM procrastinate_jobs ORDER BY id"
        )
        assert [tuple(row) for row in rows] == [
            (first, "succeeded", 2),
            (second, "succeeded", 2),
        ]
        assert await conn.fetchval(
            "SELECT indisvalid FROM pg_index "
            "WHERE indexrelid = to_regclass('procrastinate_jobs_todo_doing_lock_idx_v1')"
        )
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
