"""add knowledge bases and docs libraries

Revision ID: z2a3b4c5d6e7
Revises: y1z2a3b4c5d6
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa

revision = "z2a3b4c5d6e7"
down_revision = "y1z2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend role check to include group-admin
    op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS ck_portal_users_role")
    op.create_check_constraint(
        "ck_portal_users_role",
        "portal_users",
        "role IN ('admin', 'group-admin', 'member')",
    )

    # Knowledge bases
    op.create_table(
        "portal_knowledge_bases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "slug", name="uq_portal_kb_org_slug"),
    )
    op.create_index("ix_portal_kb_org_id", "portal_knowledge_bases", ["org_id"])

    # Group-KB access
    op.create_table(
        "portal_group_kb_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("portal_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kb_id",
            sa.Integer(),
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "kb_id", name="uq_group_kb_access"),
    )
    op.create_index("ix_group_kb_access_group_id", "portal_group_kb_access", ["group_id"])
    op.create_index("ix_group_kb_access_kb_id", "portal_group_kb_access", ["kb_id"])

    # Docs libraries
    op.create_table(
        "portal_docs_libraries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "slug", name="uq_portal_docs_org_slug"),
    )
    op.create_index("ix_portal_docs_org_id", "portal_docs_libraries", ["org_id"])

    # Group-Docs access
    op.create_table(
        "portal_group_docs_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("portal_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "library_id",
            sa.Integer(),
            sa.ForeignKey("portal_docs_libraries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "library_id", name="uq_group_docs_access"),
    )
    op.create_index("ix_group_docs_access_group_id", "portal_group_docs_access", ["group_id"])
    op.create_index("ix_group_docs_access_library_id", "portal_group_docs_access", ["library_id"])


def downgrade() -> None:
    op.drop_table("portal_group_docs_access")
    op.drop_table("portal_docs_libraries")
    op.drop_table("portal_group_kb_access")
    op.drop_table("portal_knowledge_bases")
    op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS ck_portal_users_role")
    op.create_check_constraint(
        "ck_portal_users_role",
        "portal_users",
        "role IN ('admin', 'member')",
    )
