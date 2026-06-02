"""add shield extension auth codes

Revision ID: sh2f2e3d4c5b6
Revises: sh1e1d2a3b4c
Create Date: 2026-06-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "sh2f2e3d4c5b6"
down_revision: Union[str, Sequence[str], None] = "sh1e1d2a3b4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_TENANT_IS_NULL = "NULLIF(current_setting('app.current_org_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        "portal_shield_auth_codes",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_portal_shield_auth_codes_code_hash", "portal_shield_auth_codes", ["code_hash"], unique=True)
    op.create_index("ix_portal_shield_auth_codes_expires_at", "portal_shield_auth_codes", ["expires_at"])

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_auth_codes")
    op.execute(
        "CREATE POLICY tenant_isolation ON portal_shield_auth_codes "
        f"USING (org_id = {_TENANT} OR {_TENANT_IS_NULL}) "
        f"WITH CHECK (org_id = {_TENANT} OR {_TENANT_IS_NULL})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_shield_auth_codes")
    op.drop_index("ix_portal_shield_auth_codes_expires_at", table_name="portal_shield_auth_codes")
    op.drop_index("ix_portal_shield_auth_codes_code_hash", table_name="portal_shield_auth_codes")
    op.drop_table("portal_shield_auth_codes")
