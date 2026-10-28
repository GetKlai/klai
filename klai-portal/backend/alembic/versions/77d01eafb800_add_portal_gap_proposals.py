"""add portal_gap_proposals (proposal and decision per knowledge-gap cluster)

Knowledge-gap-to-article phase 1. Pure DDL as portal_api: the table, its
constraints and indexes. Ownership, the Cat-D tenant_isolation policy and the
decided_by FK to the klai-owned portal_users live in
``post_deploy_77d01eafb800_gap_proposals_rls.sql`` (applied as klai after
this migration, code first, SQL second).

Revision ID: 77d01eafb800
Revises: b4e7c2d9a1f3
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision: str = "77d01eafb800"
down_revision: str | None = "b4e7c2d9a1f3"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_gap_proposals (
            id SERIAL PRIMARY KEY,
            org_id INTEGER NOT NULL REFERENCES portal_orgs (id) ON DELETE CASCADE,
            cluster_key VARCHAR NOT NULL,
            missing VARCHAR,
            where_kind VARCHAR(16),
            where_title VARCHAR,
            where_url VARCHAR,
            where_section VARCHAR,
            example_text VARCHAR,
            open_facts JSONB,
            language VARCHAR(8),
            model VARCHAR(64),
            generated_at TIMESTAMP WITH TIME ZONE,
            status VARCHAR(16),
            attempts INTEGER NOT NULL DEFAULT 0,
            attempted_at TIMESTAMP WITH TIME ZONE,
            failure_reason VARCHAR,
            decision VARCHAR(16) NOT NULL DEFAULT 'none',
            decided_by INTEGER,
            decided_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT uq_gap_proposals_org_cluster UNIQUE (org_id, cluster_key),
            CONSTRAINT ck_gap_proposals_where_kind CHECK (where_kind IN ('existing', 'new')),
            CONSTRAINT ck_gap_proposals_decision CHECK (decision IN ('none', 'rejected', 'out_of_scope')),
            CONSTRAINT ck_gap_proposals_status CHECK (status IN ('generating', 'ready', 'failed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gap_proposals_org_attempted ON portal_gap_proposals (org_id, attempted_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portal_gap_proposals")
