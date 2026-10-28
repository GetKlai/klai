"""add portal_slack_installations (org <-> Slack workspace link)

SPEC-SLACK-001 §6.2: one row per org and one per Slack workspace, both UNIQUE,
written by klai-slack through POST /internal/slack/link.

Application-role-safe DDL only (portal_api), the same split as
``s1p2c3a4s5e6_add_support_cases``: the table and its portal_orgs FK are
created here, and ``post_deploy_sl4c5k6i7n8s_slack_installations.sql`` hands
the table to ``klai``, adds the partner_api_keys FK (partner_api_keys is owned
by klai in production, so portal_api holds no REFERENCES on it) and the Cat-D
``tenant_isolation`` policy, applied as the klai superuser after
``alembic upgrade head``. No INSERT or UPDATE here.

Revision ID: sl4c5k6i7n8s
Revises: b4e7c2d9a1f3
Create Date: 2026-10-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "sl4c5k6i7n8s"
down_revision: str | None = "b4e7c2d9a1f3"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "portal_slack_installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("partner_api_key_id", UUID(as_uuid=False), nullable=False),
        sa.Column("installed_by_slack_user_id", sa.String(32), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", name="uq_portal_slack_installations_org_id"),
        sa.UniqueConstraint("team_id", name="uq_portal_slack_installations_team_id"),
    )


def downgrade() -> None:
    op.drop_table("portal_slack_installations")
