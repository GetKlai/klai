"""add target_path to kb_uploads (replace-a-source support)

Revision ID: b7c1d2e3f4a5
Revises: 76f43911a5ba
Create Date: 2026-09-02

A normal upload ingests under ``path = source_ref``, which is the sha256 of
the file's own bytes. That makes every re-upload of a CHANGED file a brand
new document key, so the old source stays live next to the new one — the
reason users delete the source and add it again to update it.

``target_path`` overrides the document key for one upload: it holds the path
of the source being replaced. Knowledge-ingest's ``ingest_document`` already
supersedes the active artifact under a path (soft-delete + create +
superseded_by + Qdrant clear), so ingesting under the original path IS the
replace — one row, no gap, no duplicate.

NULL for normal uploads, which keeps their ``path = source_ref`` behaviour
untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c1d2e3f4a5"
down_revision = "76f43911a5ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kb_uploads",
        sa.Column("target_path", sa.String(length=128), nullable=True),
    )
    # The poller and the sources list both look rows up by the path they are
    # about to overwrite, always scoped to one KB.
    op.create_index(
        "ix_kb_uploads_target_path",
        "kb_uploads",
        ["org_id", "kb_id", "target_path"],
        postgresql_where=sa.text("target_path IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_kb_uploads_target_path", table_name="kb_uploads")
    op.drop_column("kb_uploads", "target_path")
