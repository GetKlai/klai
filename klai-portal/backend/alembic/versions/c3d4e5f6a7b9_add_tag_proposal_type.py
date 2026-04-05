"""add 'tag' to proposal_type check constraint

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-04-05
"""

from alembic import op

revision = "c3d4e5f6a7b9"
down_revision = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_taxonomy_proposal_type", "portal_taxonomy_proposals", type_="check")
    op.create_check_constraint(
        "ck_taxonomy_proposal_type",
        "portal_taxonomy_proposals",
        "proposal_type IN ('new_node', 'merge', 'split', 'rename', 'tag')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_taxonomy_proposal_type", "portal_taxonomy_proposals", type_="check")
    op.create_check_constraint(
        "ck_taxonomy_proposal_type",
        "portal_taxonomy_proposals",
        "proposal_type IN ('new_node', 'merge', 'split', 'rename')",
    )
