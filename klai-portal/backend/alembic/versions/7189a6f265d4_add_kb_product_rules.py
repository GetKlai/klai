"""Add product_rules to portal_knowledge_bases.

Rules that say which product a document of the knowledge base is about, by the
start of its host plus URL path or its path; knowledge-ingest reads them when it
labels a chunk (knowledge-base plan, step 7). Pure DDL: the NOT NULL DEFAULT is a
metadata-only change, so no row is rewritten and no RLS policy fires. The Cat-D
tenant_isolation policy covers the new column.

Revision ID: 7189a6f265d4
Revises: f9b2d6a4c8e1
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op

revision: str = "7189a6f265d4"
down_revision: str | None = "f9b2d6a4c8e1"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE portal_knowledge_bases ADD COLUMN IF NOT EXISTS product_rules JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE portal_knowledge_bases DROP COLUMN IF EXISTS product_rules")
