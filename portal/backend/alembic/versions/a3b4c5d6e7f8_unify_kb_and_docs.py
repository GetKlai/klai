"""unify knowledge bases and docs libraries

Revision ID: a3b4c5d6e7f8
Revises: z2a3b4c5d6e7
Create Date: 2026-03-25

Merges portal_docs_libraries into portal_knowledge_bases (Library IS a KB).
Adds visibility, docs_enabled, gitea_repo_slug, owner_type, owner_user_id to portal_knowledge_bases.
Adds role to portal_group_kb_access.
Migrates existing docs library data and group access rows, then drops the old tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend portal_knowledge_bases with new columns
    op.add_column(
        "portal_knowledge_bases",
        sa.Column(
            "visibility",
            sa.Text(),
            nullable=False,
            server_default="internal",
        ),
    )
    op.create_check_constraint(
        "ck_portal_kb_visibility",
        "portal_knowledge_bases",
        "visibility IN ('public', 'internal')",
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("docs_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("gitea_repo_slug", sa.Text(), nullable=True),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column(
            "owner_type",
            sa.Text(),
            nullable=False,
            server_default="org",
        ),
    )
    op.create_check_constraint(
        "ck_portal_kb_owner_type",
        "portal_knowledge_bases",
        "owner_type IN ('org', 'user')",
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("owner_user_id", sa.Text(), nullable=True),
    )

    # 2. Add role to portal_group_kb_access
    op.add_column(
        "portal_group_kb_access",
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            server_default="viewer",
        ),
    )
    op.create_check_constraint(
        "ck_group_kb_access_role",
        "portal_group_kb_access",
        "role IN ('viewer', 'contributor', 'owner')",
    )

    # 3. Migrate docs library rows into portal_knowledge_bases
    #    Use INSERT ... ON CONFLICT DO NOTHING to handle slug overlaps
    op.execute(
        """
        INSERT INTO portal_knowledge_bases
            (org_id, name, slug, description, created_at, created_by,
             visibility, docs_enabled, owner_type)
        SELECT
            org_id, name, slug, description, created_at, created_by,
            'internal', true, 'org'
        FROM portal_docs_libraries
        ON CONFLICT (org_id, slug) DO NOTHING
        """
    )

    # 4. Migrate group_docs_access → portal_group_kb_access
    #    Join through slug to find the matching KB id
    op.execute(
        """
        INSERT INTO portal_group_kb_access
            (group_id, kb_id, granted_at, granted_by, role)
        SELECT
            gda.group_id,
            pkb.id,
            gda.granted_at,
            gda.granted_by,
            'viewer'
        FROM portal_group_docs_access gda
        JOIN portal_docs_libraries pdl ON pdl.id = gda.library_id
        JOIN portal_knowledge_bases pkb
            ON pkb.org_id = pdl.org_id AND pkb.slug = pdl.slug
        ON CONFLICT (group_id, kb_id) DO NOTHING
        """
    )

    # 5. Drop old tables (access table first due to FK)
    op.drop_table("portal_group_docs_access")
    op.drop_table("portal_docs_libraries")


def downgrade() -> None:
    # Re-create docs library tables (empty — data is not restored)
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

    # Remove added columns
    op.drop_constraint("ck_group_kb_access_role", "portal_group_kb_access", type_="check")
    op.drop_column("portal_group_kb_access", "role")

    op.drop_constraint("ck_portal_kb_owner_type", "portal_knowledge_bases", type_="check")
    op.drop_column("portal_knowledge_bases", "owner_user_id")
    op.drop_column("portal_knowledge_bases", "owner_type")
    op.drop_column("portal_knowledge_bases", "gitea_repo_slug")
    op.drop_column("portal_knowledge_bases", "docs_enabled")
    op.drop_constraint("ck_portal_kb_visibility", "portal_knowledge_bases", type_="check")
    op.drop_column("portal_knowledge_bases", "visibility")
