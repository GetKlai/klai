"""create research schema, tables and pgvector indexes

Revision ID: 0001_create_research
Revises:
Create Date: 2026-03-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_create_research"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS research")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "notebooks",
        sa.Column("id", sa.VARCHAR(32), primary_key=True),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.TEXT, nullable=False),
        sa.Column("scope", sa.VARCHAR(16), nullable=False, server_default="personal"),
        sa.Column("name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
        sa.Column("default_mode", sa.VARCHAR(16), nullable=False, server_default="narrow"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="research",
    )
    op.create_index(
        "ix_research_notebooks_tenant_id",
        "notebooks",
        ["tenant_id"],
        schema="research",
    )
    op.create_index(
        "ix_research_notebooks_owner_user_id",
        "notebooks",
        ["owner_user_id"],
        schema="research",
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.VARCHAR(32), primary_key=True),
        sa.Column("notebook_id", sa.VARCHAR(32), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.VARCHAR(16), nullable=False),
        sa.Column("name", sa.TEXT, nullable=False),
        sa.Column("original_ref", sa.TEXT, nullable=True),
        sa.Column("file_path", sa.TEXT, nullable=True),
        sa.Column("status", sa.VARCHAR(16), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.TEXT, nullable=True),
        sa.Column("chunks_count", sa.INTEGER, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["notebook_id"], ["research.notebooks.id"], ondelete="CASCADE"),
        schema="research",
    )
    op.create_index(
        "ix_research_sources_notebook_id",
        "sources",
        ["notebook_id"],
        schema="research",
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.VARCHAR(32), primary_key=True),
        sa.Column("source_id", sa.VARCHAR(32), nullable=False),
        sa.Column("notebook_id", sa.VARCHAR(32), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.TEXT, nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("embedding", sa.Text, nullable=True),  # handled by raw SQL below
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["source_id"], ["research.sources.id"], ondelete="CASCADE"),
        schema="research",
    )
    # Drop the TEXT placeholder and add real VECTOR(1024) column
    op.execute("ALTER TABLE research.chunks DROP COLUMN embedding")
    op.execute("ALTER TABLE research.chunks ADD COLUMN embedding vector(1024)")

    op.create_index(
        "ix_research_chunks_tenant_notebook",
        "chunks",
        ["tenant_id", "notebook_id"],
        schema="research",
    )
    op.create_index(
        "ix_research_chunks_source_id",
        "chunks",
        ["source_id"],
        schema="research",
    )
    # IVFFlat index for cosine similarity (Phase 2 — switch to hnsw at ~500k chunks)
    op.execute(
        "CREATE INDEX ix_research_chunks_embedding ON research.chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS research.ix_research_chunks_embedding")
    op.drop_index("ix_research_chunks_source_id", table_name="chunks", schema="research")
    op.drop_index("ix_research_chunks_tenant_notebook", table_name="chunks", schema="research")
    op.drop_table("chunks", schema="research")
    op.drop_index("ix_research_sources_notebook_id", table_name="sources", schema="research")
    op.drop_table("sources", schema="research")
    op.drop_index("ix_research_notebooks_owner_user_id", table_name="notebooks", schema="research")
    op.drop_index("ix_research_notebooks_tenant_id", table_name="notebooks", schema="research")
    op.drop_table("notebooks", schema="research")
    op.execute("DROP SCHEMA IF EXISTS research")
