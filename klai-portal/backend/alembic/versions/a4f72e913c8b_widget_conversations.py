"""widget conversation audit-trail tables

Revision ID: a4f72e913c8b
Revises: z3a4b5c6d7e8
Create Date: 2026-05-21

Adds two tables to record every chat turn that flows through
`/partner/v1/chat/completions` for a widget. The admin UI surfaces
these on the new "Activiteit" tab of the widget detail page so the
owner can review what people have asked the bot and how it
responded.

Tables live in the public schema next to ``widgets`` /
``widget_kb_access`` so the RLS helper ``_rls_current_org_id()`` is
the natural fit (Cat-D policy shape per
``.claude/rules/klai/projects/portal-security.md``).

RLS policies are NOT created here — ``portal_api`` is not the table
owner. They are applied post-deploy via
``post_deploy_a4f72e913c8b_widget_conversations_rls.sql`` as the
``klai`` superuser. See
``.claude/rules/klai/pitfalls/process-rules.md::alembic-cannot-drop-non-portal_api-tables``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4f72e913c8b"
down_revision: str | None = "z3a4b5c6d7e8"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_conversations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer,
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("widgets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_key", sa.Text, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("message_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("user_agent_hash", sa.String(64)),
        sa.Column("first_user_query", sa.Text),
        sa.Column("language_detected", sa.String(8)),
        sa.UniqueConstraint(
            "widget_id",
            "session_key",
            name="uq_widget_conversations_widget_session",
        ),
    )
    op.create_index(
        "ix_widget_conversations_widget_started",
        "widget_conversations",
        ["widget_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_widget_conversations_org_started",
        "widget_conversations",
        ["org_id", sa.text("started_at DESC")],
    )

    op.create_table(
        "widget_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger,
            sa.ForeignKey("widget_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("sources", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_widget_messages_role"),
    )
    op.create_index(
        "ix_widget_messages_conv_seq",
        "widget_messages",
        ["conversation_id", "sequence"],
    )
    op.create_index(
        "ix_widget_messages_org_created",
        "widget_messages",
        ["org_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_widget_messages_org_created", table_name="widget_messages")
    op.drop_index("ix_widget_messages_conv_seq", table_name="widget_messages")
    op.drop_table("widget_messages")
    op.drop_index("ix_widget_conversations_org_started", table_name="widget_conversations")
    op.drop_index("ix_widget_conversations_widget_started", table_name="widget_conversations")
    op.drop_table("widget_conversations")
