"""add platform admin shield tables

Revision ID: sh1e1d2a3b4c
Revises: 32fc0ed3581b, a2b3c4d5e6f7, b4c5d6e7f8g9, b5c6d7e8f9a0, c160d2b9d885, c4d5e6f7a8b9, f9e8d7c6b5a4
Create Date: 2026-06-02

Platform-admin-only Shield test surface:
- hashed browser-extension tokens
- privacy-aware Shield audit logs
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "sh1e1d2a3b4c"
down_revision: Union[str, Sequence[str], None] = (
    "32fc0ed3581b",
    "a2b3c4d5e6f7",
    "b4c5d6e7f8g9",
    "b5c6d7e8f9a0",
    "c160d2b9d885",
    "c4d5e6f7a8b9",
    "f9e8d7c6b5a4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_TENANT_IS_NULL = "NULLIF(current_setting('app.current_org_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        "portal_shield_tokens",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_portal_shield_tokens_token_hash", "portal_shield_tokens", ["token_hash"], unique=True)
    op.create_index("ix_portal_shield_tokens_org_user", "portal_shield_tokens", ["org_id", "user_id"])

    op.create_table(
        "portal_shield_logs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column(
            "token_id",
            UUID(as_uuid=False),
            sa.ForeignKey("portal_shield_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("surface", sa.String(32), nullable=False, server_default="browser_extension"),
        sa.Column("check_type", sa.String(32), nullable=False, server_default="input"),
        sa.Column("level", sa.String(16), nullable=False, server_default="basic"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("warnings", JSONB, nullable=False, server_default="[]"),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("check_type IN ('input', 'output')", name="ck_portal_shield_logs_check_type"),
        sa.CheckConstraint("level IN ('basic', 'extended', 'strict')", name="ck_portal_shield_logs_level"),
        sa.CheckConstraint("status IN ('green', 'yellow', 'orange', 'red')", name="ck_portal_shield_logs_status"),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_portal_shield_logs_risk_score"),
    )
    op.create_index("ix_portal_shield_logs_org_created_at", "portal_shield_logs", ["org_id", "created_at"])
    op.create_index("ix_portal_shield_logs_token_created_at", "portal_shield_logs", ["token_id", "created_at"])

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_tokens")
    op.execute(
        "CREATE POLICY tenant_isolation ON portal_shield_tokens "
        f"USING (org_id = {_TENANT} OR {_TENANT_IS_NULL})"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_logs")
    op.execute(
        "CREATE POLICY tenant_isolation ON portal_shield_logs "
        f"USING (org_id = {_TENANT}) WITH CHECK (org_id = {_TENANT})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_logs")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_tokens")
    op.drop_index("ix_portal_shield_logs_token_created_at", table_name="portal_shield_logs")
    op.drop_index("ix_portal_shield_logs_org_created_at", table_name="portal_shield_logs")
    op.drop_table("portal_shield_logs")
    op.drop_index("ix_portal_shield_tokens_org_user", table_name="portal_shield_tokens")
    op.drop_index("ix_portal_shield_tokens_token_hash", table_name="portal_shield_tokens")
    op.drop_table("portal_shield_tokens")
