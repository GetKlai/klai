"""add portal_org_allowed_domains table

Revision ID: 23c5c8b48669
Revises: z3a4b5c6d7e8
Create Date: 2026-04-16

Note (2026-04-22): originally authored with hand-typed rev id
`a1b2c3d4e5f6`. Renamed to a proper randomly-generated hex id
(`uuid.uuid4().hex[:12]`) as part of the SPEC-PROV-001 cleanup, so the
codebase is consistent after the collision with the (now also renamed)
`add_user_kb_preference.py` was resolved. Downstream migrations
`b2c3d4e5f6g7_add_portal_join_requests.py` and
`d1e2f3a4b5c6_add_recording_deleted_to_vexa_meetings.py` were updated
accordingly. Production ``alembic_version`` is unaffected because it
only stores the current head (`32fc0ed3581b`).
"""

from alembic import op
import sqlalchemy as sa

revision = "23c5c8b48669"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_org_allowed_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "domain", name="uq_org_allowed_domains_org_domain"),
        sa.UniqueConstraint("domain", name="uq_org_allowed_domains_domain_global"),
    )
    op.create_index("ix_portal_org_allowed_domains_domain", "portal_org_allowed_domains", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_portal_org_allowed_domains_domain", table_name="portal_org_allowed_domains")
    op.drop_table("portal_org_allowed_domains")
