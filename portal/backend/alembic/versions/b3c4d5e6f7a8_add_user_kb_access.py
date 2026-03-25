"""add portal_user_kb_access table

Revision ID: b3c4d5e6f7a8
Revises: a3b4c5d6e7f8
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_user_kb_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kb_id",
            sa.Integer(),
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "user_id", name="uq_user_kb_access"),
        sa.CheckConstraint("role IN ('viewer', 'contributor', 'owner')", name="ck_user_kb_access_role"),
    )
    op.create_index("ix_user_kb_access_kb_id", "portal_user_kb_access", ["kb_id"])
    op.create_index("ix_user_kb_access_user_id", "portal_user_kb_access", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_kb_access_user_id", table_name="portal_user_kb_access")
    op.drop_index("ix_user_kb_access_kb_id", table_name="portal_user_kb_access")
    op.drop_table("portal_user_kb_access")
