"""Drop embedding column from research.chunks (vectors now in Qdrant)

Revision ID: 0003_drop_embedding
Revises: 0002_chat_history
Create Date: 2026-03-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_drop_embedding"
down_revision: Union[str, Sequence[str], None] = "0002_chat_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS research.chunks_embedding_idx")
    op.drop_column("chunks", "embedding", schema="research")


def downgrade() -> None:
    # Re-add embedding column as nullable text (pgvector type not available after migration)
    import sqlalchemy as sa
    op.add_column(
        "chunks",
        sa.Column("embedding", sa.Text(), nullable=True),
        schema="research",
    )
