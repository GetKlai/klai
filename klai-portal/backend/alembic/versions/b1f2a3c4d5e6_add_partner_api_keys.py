"""add_partner_api_keys_and_kb_access

Revision ID: b1f2a3c4d5e6
Revises: 30f39fd7455b
Create Date: 2026-04-13 10:00:00.000000

SPEC-API-001 REQ-1.2, REQ-1.3, REQ-1.5:
- partner_api_keys table with SHA-256 hashed key storage
- partner_api_key_kb_access junction table for per-KB access levels
- RLS policies for tenant isolation (requires manual execution as klai superuser)

Operator note:
    After running `alembic upgrade head`, execute the following as the `klai`
    database superuser (portal_api role cannot ALTER TABLE ... ENABLE RLS):

        psql -U klai -d klai -f -<<'SQL'
        ALTER TABLE partner_api_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE partner_api_keys FORCE ROW LEVEL SECURITY;
        ALTER TABLE partner_api_key_kb_access ENABLE ROW LEVEL SECURITY;
        ALTER TABLE partner_api_key_kb_access FORCE ROW LEVEL SECURITY;
        SQL
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "b1f2a3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "30f39fd7455b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "partner_api_keys",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()::text"),
            primary_key=True,
        ),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "permissions",
            JSONB,
            nullable=False,
            server_default='{"chat": true, "feedback": true, "knowledge_append": false}',
        ),
        sa.Column("rate_limit_rpm", sa.Integer, nullable=False, server_default="60"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), nullable=False),
    )

    op.create_index("ix_partner_api_keys_key_hash", "partner_api_keys", ["key_hash"], unique=True)
    op.create_index("ix_partner_api_keys_org_id", "partner_api_keys", ["org_id"])

    op.create_table(
        "partner_api_key_kb_access",
        sa.Column(
            "partner_api_key_id",
            UUID(as_uuid=False),
            sa.ForeignKey("partner_api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kb_id",
            sa.Integer,
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_level", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("partner_api_key_id", "kb_id"),
    )

    # RLS policies — included for code history.
    # These CREATE POLICY statements run as the migration user.
    # ALTER TABLE ... ENABLE ROW LEVEL SECURITY must be run separately
    # as the klai superuser (see docstring).
    op.execute(
        """
        CREATE POLICY IF NOT EXISTS tenant_isolation ON partner_api_keys
            USING (org_id = current_setting('app.current_org_id')::int)
        """
    )
    op.execute(
        """
        CREATE POLICY IF NOT EXISTS tenant_isolation ON partner_api_key_kb_access
            USING (
                partner_api_key_id IN (
                    SELECT id FROM partner_api_keys
                    WHERE org_id = current_setting('app.current_org_id')::int
                )
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON partner_api_key_kb_access")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON partner_api_keys")
    op.drop_table("partner_api_key_kb_access")
    op.drop_index("ix_partner_api_keys_org_id", table_name="partner_api_keys")
    op.drop_index("ix_partner_api_keys_key_hash", table_name="partner_api_keys")
    op.drop_table("partner_api_keys")
