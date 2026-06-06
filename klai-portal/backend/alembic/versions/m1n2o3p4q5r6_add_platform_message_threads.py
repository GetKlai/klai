"""add platform message threads

Revision ID: m1n2o3p4q5r6
Revises: e9f1a2b3c4d5
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = "e9f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_message_threads",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("origin_type", sa.String(length=32), nullable=False, server_default="direct"),
        sa.Column(
            "feedback_submission_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "feedback_item_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_platform_message_threads_status"),
        sa.CheckConstraint(
            "origin_type IN ('direct', 'feedback_submission', 'feedback_item')",
            name="ck_platform_message_threads_origin_type",
        ),
    )
    op.create_index(
        "ix_platform_message_threads_org_status",
        "platform_message_threads",
        ["org_id", "status", "last_message_at"],
    )
    op.create_index("ix_platform_message_threads_last_message", "platform_message_threads", ["last_message_at"])

    op.create_table(
        "platform_message_participants",
        sa.Column(
            "thread_id",
            sa.BigInteger(),
            sa.ForeignKey("platform_message_threads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("recipient_display_name", sa.String(length=255), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_platform_message_participants_user",
        "platform_message_participants",
        ["org_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_platform_message_participants_thread",
        "platform_message_participants",
        ["thread_id"],
    )

    op.create_table(
        "platform_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.BigInteger(),
            sa.ForeignKey("platform_message_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_type", sa.String(length=32), nullable=False),
        sa.Column("sender_user_id", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "sender_type IN ('platform_admin', 'user', 'system')",
            name="ck_platform_messages_sender_type",
        ),
    )
    op.create_index("ix_platform_messages_thread_created", "platform_messages", ["thread_id", "created_at"])
    op.create_index("ix_platform_messages_org_created", "platform_messages", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_platform_messages_org_created", table_name="platform_messages")
    op.drop_index("ix_platform_messages_thread_created", table_name="platform_messages")
    op.drop_table("platform_messages")
    op.drop_index("ix_platform_message_participants_thread", table_name="platform_message_participants")
    op.drop_index("ix_platform_message_participants_user", table_name="platform_message_participants")
    op.drop_table("platform_message_participants")
    op.drop_index("ix_platform_message_threads_last_message", table_name="platform_message_threads")
    op.drop_index("ix_platform_message_threads_org_status", table_name="platform_message_threads")
    op.drop_table("platform_message_threads")

