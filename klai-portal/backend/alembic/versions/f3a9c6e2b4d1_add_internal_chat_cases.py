"""add internal_chat_cases (the case an internal conversation carries to its next turn)

The help widget keeps its case and the passages its selection chose with the
assistant turn in ``widget_messages.answer_signals`` and reads them back on the
next turn. The internal chat cannot reuse that table: a ``widget_conversations``
row needs a widget and ``widget_messages`` holds the transcript, while the
internal chat keeps no text of the conversation (``internal_chat_turns`` holds
signals only). This table holds only what the next turn reads: one row per
conversation, keyed by digests of the employee and the conversation's first
message, with the case and at most three kept passages, pruned after 30 days
by the writer (``widget_audit.keep_internal_case``).

Same split as ``i5c6t7u8r9n0_add_internal_chat_turns``: pure DDL here, and
``post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql`` hands the table to
``klai`` and adds the Cat-D ``tenant_isolation`` policy, applied as the klai
superuser after ``alembic upgrade head``. No INSERT or UPDATE here.

Revision ID: f3a9c6e2b4d1
Revises: e8b3c5a1f9d2
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f3a9c6e2b4d1"
down_revision = "e8b3c5a1f9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_chat_cases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_key", sa.String(64), nullable=False),
        sa.Column("turn_digest", sa.String(64), nullable=False),
        sa.Column("case_file", JSONB(), nullable=True),
        sa.Column("kept_passages", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "conversation_key", name="uq_internal_chat_cases_org_key"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_internal_chat_cases_org_updated",
        "internal_chat_cases",
        ["org_id", "updated_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_internal_chat_cases_org_updated", table_name="internal_chat_cases", if_exists=True)
    op.drop_table("internal_chat_cases", if_exists=True)
