"""Revisions cb73fd8d4df4 and 77a892663768 give the installed procrastinate 3.12 a working queue.

Two starting points, each migrated, used, migrated again and used again, so
every branch the revisions take on a real database runs: a procrastinate 3.10
queue (cb73fd8d4df4 applies the pre-deploy files, 77a892663768 drops the 3.10
objects), and an empty schema (cb73fd8d4df4 installs the full 3.12 schema,
77a892663768 finds nothing to drop); the second run of each is the
already-migrated database.

3.10 code running on the migrated schema is not tested: after 77a892663768 it
cannot defer at all, which is the point of that revision, and no image that an
automatic rollback can restore runs 3.10 any more.

The 3.10 schema is generated at test time from procrastinate 3.10.0 in a
throwaway ``uv`` environment. Each case gets a schema of its own, so the
database's own queue tables are left alone. Needs
``GRAPH_REFRESH_BUDGET_TEST_DSN``, ``uv`` and the real procrastinate (libpq).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from pathlib import Path
from types import ModuleType

import asyncpg
import pytest

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic/versions"
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
_OLD_OBJECTS = """
    SELECT coalesce(array_agg(name ORDER BY name), '{}') FROM (
        SELECT proname AS name FROM pg_proc
        WHERE pronamespace = current_schema()::regnamespace
          AND proname IN ('procrastinate_defer_jobs_v1', 'procrastinate_defer_periodic_job_v2')
        UNION ALL
        SELECT typname FROM pg_type
        WHERE typnamespace = current_schema()::regnamespace
          AND typname = 'procrastinate_job_to_defer_v1'
        UNION ALL
        SELECT relname FROM pg_class
        WHERE relnamespace = current_schema()::regnamespace
          AND relname = 'procrastinate_jobs_id_lock_idx_v1'
    ) AS old
"""
_ALL_OLD_OBJECTS = [
    "procrastinate_defer_jobs_v1",
    "procrastinate_defer_periodic_job_v2",
    "procrastinate_job_to_defer_v1",
    "procrastinate_jobs_id_lock_idx_v1",
]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


def _revision(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(filename, _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PRE = _revision("0017_procrastinate_3_12_pre_deploy.py")
_POST = _revision("0018_procrastinate_3_12_post_deploy.py")


@pytest.fixture
def procrastinate_with_libpq(real_procrastinate):
    if not real_procrastinate.has_libpq:
        message = "the psycopg connector needs libpq"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return real_procrastinate


@pytest.fixture
async def queue_schema():
    """A fresh schema to build a queue in, and a connection whose search_path is it."""
    schema = f"queue_{uuid.uuid4().hex[:12]}"
    assert _DSN is not None
    conn = await asyncpg.connect(_DSN, server_settings={"search_path": schema})
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield schema, conn
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


async def _upgrade(revision: ModuleType, schema: str) -> None:
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


async def _assert_3_12_queue(conn, job_ids: list[int]) -> None:
    rows = await conn.fetch(
        "SELECT id, status::text AS status, attempts FROM procrastinate_jobs ORDER BY id"
    )
    assert [tuple(row) for row in rows] == [(job_id, "succeeded", 2) for job_id in job_ids]
    assert await conn.fetchval(_OLD_OBJECTS) == []
    assert await conn.fetchval(
        "SELECT indisvalid FROM pg_index "
        "WHERE indexrelid = to_regclass('procrastinate_jobs_todo_doing_lock_idx_v1')"
    )


async def test_a_3_10_queue_runs_3_12_jobs_after_both_revisions_and_after_running_them_again(
    procrastinate_with_libpq, queue_schema
) -> None:
    schema, conn = queue_schema
    schema_3_10 = subprocess.run(  # noqa: S603 - fixed argv, no input from outside the test
        _PRINT_SCHEMA_3_10,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    await conn.execute(schema_3_10)

    job_ids = []
    await _upgrade(_PRE, schema)
    job_ids.append(await _round_trip(procrastinate_with_libpq, schema))
    await _upgrade(_PRE, schema)
    # The pre-deploy revision keeps everything 3.10 code needs.
    assert await conn.fetchval(_OLD_OBJECTS) == _ALL_OLD_OBJECTS
    for _ in range(2):
        await _upgrade(_POST, schema)
        job_ids.append(await _round_trip(procrastinate_with_libpq, schema))

    await _assert_3_12_queue(conn, job_ids)


async def test_an_empty_schema_gets_a_3_12_queue_from_both_revisions_and_keeps_it_on_a_second_run(
    procrastinate_with_libpq, queue_schema
) -> None:
    schema, conn = queue_schema

    job_ids = []
    for _ in range(2):
        await _upgrade(_PRE, schema)
        await _upgrade(_POST, schema)
        job_ids.append(await _round_trip(procrastinate_with_libpq, schema))

    await _assert_3_12_queue(conn, job_ids)
