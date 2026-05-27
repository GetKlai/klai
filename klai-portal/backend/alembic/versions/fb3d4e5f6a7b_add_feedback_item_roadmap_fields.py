"""add feedback item roadmap fields

Revision ID: fb3d4e5f6a7b
Revises: fb2c3d4e5f6a
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fb3d4e5f6a7b"
down_revision: Union[str, Sequence[str], None] = "fb2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("feedback_items", sa.Column("public_feedback_url", sa.String(length=2048), nullable=True))
    op.add_column("feedback_items", sa.Column("public_title", sa.String(length=256), nullable=True))
    op.add_column("feedback_items", sa.Column("public_summary", sa.Text(), nullable=True))
    op.add_column("feedback_items", sa.Column("target_window", sa.String(length=64), nullable=True))
    op.add_column("feedback_items", sa.Column("owner", sa.String(length=128), nullable=True))
    op.add_column("feedback_items", sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback_items", "shipped_at")
    op.drop_column("feedback_items", "owner")
    op.drop_column("feedback_items", "target_window")
    op.drop_column("feedback_items", "public_summary")
    op.drop_column("feedback_items", "public_title")
    op.drop_column("feedback_items", "public_feedback_url")
