"""create scribe schema and transcriptions table

Revision ID: 0001_create_scribe
Revises:
Create Date: 2026-03-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_create_scribe"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS scribe")
    op.create_table(
        "transcriptions",
        sa.Column("id", sa.VARCHAR(32), primary_key=True),
        sa.Column("user_id", sa.VARCHAR(128), nullable=False),
        sa.Column("text", sa.TEXT, nullable=False),
        sa.Column("language", sa.VARCHAR(16), nullable=False),
        sa.Column("duration_seconds", sa.NUMERIC(8, 2), nullable=False),
        sa.Column("inference_time_seconds", sa.NUMERIC(8, 2), nullable=False),
        sa.Column("provider", sa.VARCHAR(64), nullable=False),
        sa.Column("model", sa.VARCHAR(128), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="scribe",
    )
    op.create_index(
        "ix_scribe_transcriptions_user_id",
        "transcriptions",
        ["user_id"],
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_index("ix_scribe_transcriptions_user_id", table_name="transcriptions", schema="scribe")
    op.drop_table("transcriptions", schema="scribe")
    op.execute("DROP SCHEMA IF EXISTS scribe")
