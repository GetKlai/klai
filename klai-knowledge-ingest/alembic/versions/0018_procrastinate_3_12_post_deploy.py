"""Drop the procrastinate 3.10 objects that 3.12 no longer uses (the contract step).

This applies the upstream post-deploy migrations of 3.12, read from the
installed package like revision cb73fd8d4df4 applied the pre-deploy ones:

- 03.12.00_50 drops procrastinate_defer_jobs_v1, its procrastinate_job_to_defer_v1
  type and procrastinate_defer_periodic_job_v2, which only 3.10 code calls
- 03.12.00_51 drops the (id, lock) index, replaced by
  procrastinate_jobs_todo_doing_lock_idx_v1, CONCURRENTLY and so outside a
  transaction

procrastinate 3.10 code cannot defer a job on the result, so this ships on its
own, after every image an automatic rollback could restore runs 3.12.

Both files consist of DROP ... IF EXISTS statements only, so they need no guard:
on a database where they already ran, and on a fresh one that got the full 3.12
schema from cb73fd8d4df4 and never had these objects, they do nothing (Postgres
skips a missing argument type in DROP FUNCTION IF EXISTS with a notice). For the
same reason ``alembic upgrade --sql`` prints exactly what an online run does.
A concurrent drop that is interrupted can leave the index invalid; IF EXISTS
drops it on the next run.

Revision ID: 77a892663768
Revises: cb73fd8d4df4
Create Date: 2026-11-24
"""

from collections.abc import Sequence
from importlib.metadata import distribution

from alembic import op

revision: str = "77a892663768"
down_revision: str | None = "cb73fd8d4df4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POST_DEPLOY = (
    "03.12.00_50_post_add_lock_mode.sql",
    "03.12.00_51_post_drop_id_lock_index.sql",
)


def _sql(name: str) -> str:
    # Read from the installed files rather than through importlib.resources:
    # importing procrastinate loads psycopg, which needs libpq, and neither
    # ``alembic heads`` nor ``alembic upgrade --sql`` should.
    return (
        distribution("procrastinate")
        .locate_file(f"procrastinate/sql/migrations/{name}")
        .read_text(encoding="utf-8")
    )


def upgrade() -> None:
    context = op.get_context()
    # One script per file, as upstream's own Alembic revisions run them: a
    # multi-statement script is one implicit transaction, in which DROP INDEX
    # CONCURRENTLY refuses to run.
    with context.autocommit_block():
        for name in _POST_DEPLOY:
            if context.as_sql:
                context.impl.static_output(_sql(name))
                continue
            # Runs a multi-statement script, which asyncpg refuses as a prepared statement.
            from procrastinate.contrib.alembic.migrations_utils import execute_script

            execute_script(_sql(name))


def downgrade() -> None:
    raise NotImplementedError("procrastinate SQL migrations are irreversible")
