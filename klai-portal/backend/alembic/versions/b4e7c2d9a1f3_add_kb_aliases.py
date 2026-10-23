"""Add aliases to portal_knowledge_bases.

Groups of names for one thing per knowledge base (a product and its synonyms),
read by knowledge-ingest for the context prefix of a chunk and by retrieval-api
to expand a query. Pure DDL: the NOT NULL DEFAULT is a metadata-only change, so
no row is rewritten and no RLS policy fires. The Cat-D tenant_isolation policy
covers the new column.

Revision ID: b4e7c2d9a1f3
Revises: f9b2d6a4c8e1
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op

revision: str = "b4e7c2d9a1f3"
down_revision: str | None = "f9b2d6a4c8e1"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE portal_knowledge_bases ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE portal_knowledge_bases DROP COLUMN IF EXISTS aliases")
