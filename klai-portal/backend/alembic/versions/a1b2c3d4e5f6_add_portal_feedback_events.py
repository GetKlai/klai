"""Add portal_feedback_events table.

Revision ID: a1b2c3d4e5f6
Revises: z2a3b4c5d6e7
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_feedback_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column("chunk_ids", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("correlated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("model_alias", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "rating IN ('thumbsUp', 'thumbsDown')",
            name="ck_feedback_events_rating",
        ),
        sa.UniqueConstraint("message_id", "conversation_id", name="uq_feedback_events_msg_conv"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_events_org_occurred",
        "portal_feedback_events",
        ["org_id", "occurred_at"],
        unique=False,
    )

    # RLS policies (identical to portal_retrieval_gaps pattern)
    op.execute("ALTER TABLE portal_feedback_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'portal_feedback_events' AND policyname = 'feedback_events_select_policy'
            ) THEN
                CREATE POLICY feedback_events_select_policy ON portal_feedback_events
                    FOR SELECT USING (org_id = current_setting('app.current_org_id')::integer);
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'portal_feedback_events' AND policyname = 'feedback_events_insert_policy'
            ) THEN
                CREATE POLICY feedback_events_insert_policy ON portal_feedback_events
                    FOR INSERT WITH CHECK (true);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_events_insert_policy ON portal_feedback_events")
    op.execute("DROP POLICY IF EXISTS feedback_events_select_policy ON portal_feedback_events")
    op.drop_index("ix_feedback_events_org_occurred")
    op.drop_table("portal_feedback_events")
