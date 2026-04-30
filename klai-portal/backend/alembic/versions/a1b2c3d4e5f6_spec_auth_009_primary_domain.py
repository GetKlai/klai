"""SPEC-AUTH-009: add primary_domain + auto_accept_same_domain to portal_orgs;
   drop portal_org_allowed_domains.

Revision ID: a1b2c3d4e5f6
Revises: z3a4b5c6d7e8
Create Date: 2026-04-30
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SPEC-AUTH-009 A-001: Pre-launch; backfill placeholder for any existing test rows.
    # In a real migration with customer data, this would need a careful per-row UPDATE
    # to set the actual founder domain. For pre-launch we use a placeholder.
    op.execute("""
        UPDATE portal_orgs
        SET primary_domain = COALESCE(
            (SELECT split_part(u.email, '@', 2)
             FROM portal_users u
             WHERE u.org_id = portal_orgs.id
               AND u.role = 'admin'
               AND u.email IS NOT NULL
             ORDER BY u.created_at ASC
             LIMIT 1),
            'placeholder.invalid'
        )
        WHERE primary_domain IS NULL OR primary_domain = ''
    """)

    # Add primary_domain as NOT NULL (backfill above ensures no NULLs)
    op.add_column(
        "portal_orgs",
        sa.Column("primary_domain", sa.String(253), nullable=False, server_default=""),
    )

    # Add auto_accept_same_domain with default False
    op.add_column(
        "portal_orgs",
        sa.Column("auto_accept_same_domain", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Partial index for fast domain lookups (WHERE deleted_at IS NULL)
    op.create_index(
        "ix_portal_orgs_primary_domain",
        "portal_orgs",
        ["primary_domain"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # R2: Drop the SPEC-AUTH-006 allowed-domains table (pre-launch, no migration needed)
    op.drop_table("portal_org_allowed_domains")


def downgrade() -> None:
    # Recreate portal_org_allowed_domains for reversibility
    op.create_table(
        "portal_org_allowed_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["portal_orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "domain", name="uq_org_allowed_domains_org_domain"),
        sa.UniqueConstraint("domain", name="uq_org_allowed_domains_domain_global"),
    )

    op.drop_index("ix_portal_orgs_primary_domain", table_name="portal_orgs")
    op.drop_column("portal_orgs", "auto_accept_same_domain")
    op.drop_column("portal_orgs", "primary_domain")
