"""add internal_chat_turns (one record of signals per internal-chat turn)

One-chat-pipeline slice 5 (docs/architecture/chat-quality-history-and-plan.md
§7.2, "Vastleggen per beurt"). The widget keeps its per-answer signals in
``widget_messages.answer_signals``; the internal chat only had log lines. This
table is the internal counterpart, written by the same code: one row per turn
with the signals (decision, band, grounding outcome, sub-question count, model,
timings) and deliberately no message text and no user, because LibreChat
already holds the conversation.

Application-role-safe DDL only (portal_api), same split as
``s1p2c3a4s5e6_add_support_cases``: the table is created here as pure DDL, and
``post_deploy_i5c6t7u8r9n0_internal_chat_turns_rls.sql`` hands it to ``klai``
and adds the Cat-D ``tenant_isolation`` policy, applied as the klai superuser
after ``alembic upgrade head``. No INSERT or UPDATE here.

Revision ID: i5c6t7u8r9n0
Revises: s2r3v4w5e6r7
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "i5c6t7u8r9n0"
down_revision = "s2r3v4w5e6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "internal_chat_turns",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answer_signals", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_internal_chat_turns_org_created", "internal_chat_turns", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_internal_chat_turns_org_created", table_name="internal_chat_turns")
    op.drop_table("internal_chat_turns")
