"""SPEC-INGEST-UNIQUE-ARTIFACT-001 -- partial UNIQUE index on knowledge.artifacts.

Closes the race window where two concurrent ``ingest_document()`` calls with
identical ``(org_id, kb_slug, path)`` both pass ``get_active_content_hash``
(see no active row), both call ``soft_delete_artifact`` (idempotent), and
both call ``create_artifact`` -- leaving two active rows for the same path.

The active-row predicate is ``belief_time_end = 253402300800`` (the
``9999-12-31`` sentinel meaning "still current"). Soft-deleted rows have
``belief_time_end < sentinel`` and are excluded from the unique index, so
re-ingesting the same path after a soft-delete remains valid.

Pre-flight on prod 2026-05-06: 0 duplicate active rows across all tenants
(including Voys), so this migration runs without a cleanup pass.

CREATE INDEX CONCURRENTLY cannot run inside a transaction block. Alembic
wraps every migration in a transaction by default; we commit it before the
index command and let PostgreSQL handle the rest in auto-commit mode.

Revision ID: 9a3c4d5e6f7b
Revises: dd1b439a57d0
Create Date: 2026-05-06
SPEC: SPEC-INGEST-UNIQUE-ARTIFACT-001
Audit: 2026-05-06 finding 7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9a3c4d5e6f7b"
down_revision: str | None = "dd1b439a57d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Break out of alembic's implicit transaction so CREATE INDEX
    # CONCURRENTLY is allowed.
    op.execute("COMMIT")
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_artifacts_active_path "
        "ON knowledge.artifacts (org_id, kb_slug, path) "
        "WHERE belief_time_end = 253402300800"
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS knowledge.uq_artifacts_active_path")
