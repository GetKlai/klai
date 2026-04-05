"""add taxonomy_node_ids column to portal_retrieval_gaps with GIN index

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "b2c3d4e5f6a8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_retrieval_gaps",
        sa.Column("taxonomy_node_ids", ARRAY(sa.Integer()), nullable=True),
    )
    op.create_index(
        "ix_retrieval_gaps_taxonomy",
        "portal_retrieval_gaps",
        ["taxonomy_node_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_gaps_taxonomy", table_name="portal_retrieval_gaps")
    op.drop_column("portal_retrieval_gaps", "taxonomy_node_ids")
