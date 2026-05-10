"""Add kb_uploads tracking table.

SPEC-KB-FILE-UPLOAD-001 — persists per-upload state so background
polling against docling-serve survives portal-api restarts. The table
holds:

- ``id`` — UUID primary key, also returned to the frontend for status
  polling (`GET /api/app/.../sources/file/{id}/status`).
- ``kb_id`` / ``org_id`` — tenant scoping. RLS policies live in the
  paired ``post_deploy_85e5d0a7cb98_kb_uploads_rls.sql`` file (cat-D
  per ``portal-security.md``).
- ``filename`` / ``extension`` / ``bytes`` / ``mime`` — what the
  user uploaded.
- ``status`` — the workflow phase: ``processing`` (docling crunching)
  → ``ingesting`` (markdown handed to /ingest/v1/document) → ``done``,
  or ``failed`` with ``failure_reason``.
- ``docling_task_id`` — the async task we poll. Null for text-path
  uploads that bypass docling.
- ``artifact_id`` — populated once knowledge-ingest accepts the
  markdown. The frontend uses this to navigate to the resulting source.
- ``failure_reason`` — structured enum string (one of the codes in
  ``app/services/file_upload.py``).
- ``created_at`` / ``updated_at`` — timeline for audit + cleanup.

Indexes:
- ``(org_id, kb_id, status)`` — what the poller queries.
- ``docling_task_id UNIQUE`` — defensive against duplicate polls.
- ``(org_id, kb_id, source_ref)`` — content-addressed dedup lookup.

Revision: 85e5d0a7cb98
Down-revision: h1i2j3k4l5m6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "85e5d0a7cb98"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_uploads",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kb_id",
            sa.Integer,
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("mime", sa.String(127), nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_reason", sa.String(64), nullable=True),
        sa.Column("docling_task_id", sa.String(128), nullable=True),
        sa.Column("artifact_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'ingesting', 'done', 'failed')",
            name="ck_kb_uploads_status",
        ),
    )
    op.create_index(
        "ix_kb_uploads_poller",
        "kb_uploads",
        ["status", "updated_at"],
        postgresql_where=sa.text("status IN ('processing', 'ingesting')"),
    )
    op.create_index(
        "ix_kb_uploads_org_kb",
        "kb_uploads",
        ["org_id", "kb_id", "created_at"],
    )
    op.create_index(
        "ix_kb_uploads_docling_task_id",
        "kb_uploads",
        ["docling_task_id"],
        unique=True,
        postgresql_where=sa.text("docling_task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_kb_uploads_source_ref",
        "kb_uploads",
        ["org_id", "kb_id", "source_ref"],
    )

    # RLS enable + policy creation runs as ``klai`` superuser via
    # paired post_deploy SQL — portal_api role cannot ENABLE RLS on a
    # table it does not own. See ``portal-security.md`` § "RLS + Alembic".


def downgrade() -> None:
    op.drop_index("ix_kb_uploads_source_ref", table_name="kb_uploads")
    op.drop_index("ix_kb_uploads_docling_task_id", table_name="kb_uploads")
    op.drop_index("ix_kb_uploads_org_kb", table_name="kb_uploads")
    op.drop_index("ix_kb_uploads_poller", table_name="kb_uploads")
    op.drop_table("kb_uploads")
