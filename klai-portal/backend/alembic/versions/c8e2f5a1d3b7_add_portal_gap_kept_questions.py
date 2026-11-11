"""add portal_gap_kept_questions (a widget gap question kept past the message retention)

Owner decision of 2026-11-11 (docs/architecture/knowledge-gap-to-article.md
§6): for an org at telemetry full, the question of a counted widget gap turn
is kept 28 days instead of the seven of widget_messages, while its group is
undecided. Pure DDL as portal_api; ownership, the Cat-D tenant_isolation
policy and the grants live in
``post_deploy_c8e2f5a1d3b7_gap_kept_questions_rls.sql`` (applied as klai after
this migration). No backfill: the retention sweep writes a row only for a turn
it deletes from now on.

Revision ID: c8e2f5a1d3b7
Revises: sl4c5k6i7n8s
Create Date: 2026-11-11
"""

from __future__ import annotations

from alembic import op

revision: str = "c8e2f5a1d3b7"
down_revision: str | None = "sl4c5k6i7n8s"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_gap_kept_questions (
            id BIGSERIAL PRIMARY KEY,
            org_id INTEGER NOT NULL REFERENCES portal_orgs (id) ON DELETE CASCADE,
            message_id BIGINT NOT NULL,
            conversation_ref VARCHAR(64) NOT NULL,
            cluster_key VARCHAR(32) NOT NULL,
            question VARCHAR(200) NOT NULL,
            signals JSONB NOT NULL,
            turn_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT uq_gap_kept_questions_org_message UNIQUE (org_id, message_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gap_kept_questions_org_turn_at ON portal_gap_kept_questions (org_id, turn_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portal_gap_kept_questions")
