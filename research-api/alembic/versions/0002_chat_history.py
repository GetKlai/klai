"""Add save_history to notebooks and create chat_messages table

Revision ID: 0002_chat_history
Revises: 0001_create_research
Create Date: 2026-03-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_chat_history"
down_revision: Union[str, Sequence[str], None] = "0001_create_research"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notebooks",
        sa.Column("save_history", sa.Boolean(), nullable=False, server_default="true"),
        schema="research",
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.VARCHAR(32), primary_key=True),
        sa.Column("notebook_id", sa.VARCHAR(32), nullable=False),
        sa.Column("tenant_id", sa.VARCHAR(64), nullable=False),
        sa.Column("role", sa.VARCHAR(16), nullable=False),
        sa.Column("content", sa.TEXT, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["research.notebooks.id"], ondelete="CASCADE"
        ),
        schema="research",
    )
    op.create_index(
        "ix_research_chat_messages_notebook_id",
        "chat_messages",
        ["notebook_id"],
        schema="research",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_chat_messages_notebook_id",
        table_name="chat_messages",
        schema="research",
    )
    op.drop_table("chat_messages", schema="research")
    op.drop_column("notebooks", "save_history", schema="research")
