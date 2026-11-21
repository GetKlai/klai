"""Bring the procrastinate job queue from the 3.10 schema to the 3.12 one.

procrastinate 3.12 defers through ``procrastinate_defer_jobs_v2`` and its
``procrastinate_job_to_defer_v2`` type, so its code fails every defer on a 3.10
schema. This applies the upstream pre-deploy migrations of 3.11 and 3.12, read
from the installed package so the SQL is always the one that package expects:

- 03.11.00_01 fetch_job_v2 accepts a job without a lock before the lock subquery
- 03.12.00_01 lock_mode column and enum, defer_jobs_v2, defer_periodic_job_v3
- 03.12.00_02 retry_job_v2 fails a ``doing`` job whose queueing lock a ``todo``
  job holds instead of raising
- 03.12.00_03 index on ``lock`` for todo/doing jobs, built CONCURRENTLY and so
  outside a transaction

Each step is skipped when its result is already there, so the revision is
safe on a database already at the 3.12 schema and when run again. A database
without the queue tables (a fresh dev or CI stack) gets the installed
package's full schema instead. Every object 3.10 code uses stays, so the
previous image still runs on the result: the post-deploy migrations
03.12.00_50 and 03.12.00_51 drop the old defer functions and the (id, lock)
index and ship in a later revision.

Revision ID: cb73fd8d4df4
Revises: 7c3e9a41d2b6
Create Date: 2026-11-21
"""

from collections.abc import Sequence
from importlib.metadata import distribution

import sqlalchemy as sa

from alembic import op

revision: str = "cb73fd8d4df4"
down_revision: str | None = "7c3e9a41d2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FETCH_SKIP_NULL_LOCK = "03.11.00_01_pre_fetch_job_skip_null_lock.sql"
ADD_LOCK_MODE = "03.12.00_01_pre_add_lock_mode.sql"
RETRY_FAIL_ON_QUEUEING_LOCK = "03.12.00_02_pre_retry_job_fail_on_queueing_lock_conflict.sql"
TODO_DOING_LOCK_INDEX = "03.12.00_03_pre_add_todo_doing_lock_index.sql"
_PRE_DEPLOY = (
    FETCH_SKIP_NULL_LOCK,
    ADD_LOCK_MODE,
    RETRY_FAIL_ON_QUEUEING_LOCK,
    TODO_DOING_LOCK_INDEX,
)


def _sql(path: str) -> str:
    # Read from the installed files rather than through importlib.resources:
    # importing procrastinate loads psycopg, which needs libpq, and neither
    # ``alembic heads`` nor ``alembic upgrade --sql`` should.
    return (
        distribution("procrastinate")
        .locate_file(f"procrastinate/sql/{path}")
        .read_text(encoding="utf-8")
    )


def _scalar(query: str) -> object:
    return op.get_bind().scalar(sa.text(query))


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        for name in _PRE_DEPLOY[:-1]:
            context.impl.static_output(_sql(f"migrations/{name}"))
        with context.autocommit_block():
            context.impl.static_output(_sql(f"migrations/{TODO_DOING_LOCK_INDEX}"))
        return

    # Runs a multi-statement script, which asyncpg refuses as a prepared statement.
    from procrastinate.contrib.alembic.migrations_utils import execute_script

    if _scalar("SELECT to_regclass('procrastinate_jobs')") is None:
        execute_script(_sql("schema.sql"))
        return

    # 03.12.00_01 replaces fetch_job_v2 again, so 03.11.00_01 only runs with it:
    # on its own it would put back the fetch that ignores lock_mode.
    if _scalar("SELECT to_regtype('procrastinate_lock_mode')") is None:
        execute_script(_sql(f"migrations/{FETCH_SKIP_NULL_LOCK}"))
        execute_script(_sql(f"migrations/{ADD_LOCK_MODE}"))

    retry_catches_conflict = _scalar(
        "SELECT prosrc LIKE '%unique_violation%' FROM pg_proc WHERE oid = to_regprocedure("
        "'procrastinate_retry_job_v2(bigint, timestamptz, integer, varchar, varchar)')"
    )
    if not retry_catches_conflict:
        execute_script(_sql(f"migrations/{RETRY_FAIL_ON_QUEUEING_LOCK}"))

    index_valid = _scalar(
        "SELECT indisvalid FROM pg_index "
        "WHERE indexrelid = to_regclass('procrastinate_jobs_todo_doing_lock_idx_v1')"
    )
    if index_valid:
        return
    with context.autocommit_block():
        if index_valid is False:
            # A failed concurrent build leaves an invalid index behind, and the
            # upstream file cannot run while it exists.
            execute_script("DROP INDEX CONCURRENTLY procrastinate_jobs_todo_doing_lock_idx_v1")
        execute_script(_sql(f"migrations/{TODO_DOING_LOCK_INDEX}"))


def downgrade() -> None:
    # The previous image runs on the upgraded schema (every 3.10 object stays),
    # so a rollback never needs this.
    raise NotImplementedError("procrastinate SQL migrations are irreversible")
