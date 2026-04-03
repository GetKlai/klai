"""Add default_org_role to portal_knowledge_bases."""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("default_org_role", sa.Text(), nullable=True, server_default=sa.text("'viewer'")),
    )


def downgrade() -> None:
    op.drop_column("portal_knowledge_bases", "default_org_role")
