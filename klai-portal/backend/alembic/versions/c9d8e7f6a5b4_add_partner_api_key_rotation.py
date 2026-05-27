"""add partner API key rotation metadata

Revision ID: c9d8e7f6a5b4
Revises: fb1c2d3e4a5b
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "fb1c2d3e4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("partner_api_keys", sa.Column("rotated_from_key_id", UUID(as_uuid=False), nullable=True))
    op.add_column("partner_api_keys", sa.Column("rotated_to_key_id", UUID(as_uuid=False), nullable=True))
    op.add_column("partner_api_keys", sa.Column("rotation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_partner_api_keys_rotated_from",
        "partner_api_keys",
        "partner_api_keys",
        ["rotated_from_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_partner_api_keys_rotated_to",
        "partner_api_keys",
        "partner_api_keys",
        ["rotated_to_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_partner_api_keys_rotated_from_key_id", "partner_api_keys", ["rotated_from_key_id"])
    op.create_index("ix_partner_api_keys_rotated_to_key_id", "partner_api_keys", ["rotated_to_key_id"])


def downgrade() -> None:
    op.drop_index("ix_partner_api_keys_rotated_to_key_id", table_name="partner_api_keys")
    op.drop_index("ix_partner_api_keys_rotated_from_key_id", table_name="partner_api_keys")
    op.drop_constraint("fk_partner_api_keys_rotated_to", "partner_api_keys", type_="foreignkey")
    op.drop_constraint("fk_partner_api_keys_rotated_from", "partner_api_keys", type_="foreignkey")
    op.drop_column("partner_api_keys", "rotation_started_at")
    op.drop_column("partner_api_keys", "rotated_to_key_id")
    op.drop_column("partner_api_keys", "rotated_from_key_id")
