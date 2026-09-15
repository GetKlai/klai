"""gap provenance: conversation_id + language on portal_retrieval_gaps

Revision ID: d8b3f6a1c4e9
Revises: c2a7e9d4b1f6
Create Date: 2026-09-15

SPEC-KNOWLEDGE-ACTIVITY-001 §4.5. Columns only — the FOREIGN KEY on
widget_conversations is NOT created here: portal_api (the role alembic runs
as) has no REFERENCES privilege on that klai-owned table, so adding it in
``upgrade()`` would fail 42501 and crashloop the container. It ships in
post_deploy_d8b3f6a1c4e9_gaps_conversation_fk.sql, applied as klai superuser.
``portal_retrieval_gaps`` itself is portal_api-owned, so the plain ADD COLUMNs
below are safe; both are nullable, so no backfill is needed.
"""

from alembic import op
import sqlalchemy as sa

revision = "d8b3f6a1c4e9"
down_revision = "c2a7e9d4b1f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_retrieval_gaps", sa.Column("conversation_id", sa.BigInteger(), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("language", sa.String(length=8), nullable=True))
    op.create_index(
        "ix_retrieval_gaps_conversation",
        "portal_retrieval_gaps",
        ["conversation_id"],
    )


def downgrade() -> None:
    # Dropping conversation_id takes the post-deploy foreign key with it
    # (Postgres drops a column's constraints with the column).
    op.drop_index("ix_retrieval_gaps_conversation", table_name="portal_retrieval_gaps")
    op.drop_column("portal_retrieval_gaps", "language")
    op.drop_column("portal_retrieval_gaps", "conversation_id")
