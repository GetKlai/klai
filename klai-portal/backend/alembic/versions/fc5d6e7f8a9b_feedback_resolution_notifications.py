"""feedback resolution notifications

Revision ID: fc5d6e7f8a9b
Revises: a4b5c6d7e8f9
Create Date: 2026-05-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fc5d6e7f8a9b"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_feedback_items_status", "feedback_items", type_="check")
    op.create_check_constraint(
        "ck_feedback_items_status",
        "feedback_items",
        "status IN ('inbox', 'under_review', 'planned', 'in_progress', 'shipped', 'resolved', 'wont_do')",
    )
    op.add_column("feedback_items", sa.Column("resolution_summary", sa.Text(), nullable=True))
    op.add_column("feedback_items", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("feedback_items", sa.Column("resolved_by", sa.String(length=64), nullable=True))
    op.add_column(
        "feedback_items",
        sa.Column("notification_state", sa.String(length=32), nullable=False, server_default="not_needed"),
    )

    op.create_table(
        "feedback_notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "item_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submission_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("subject", sa.String(length=256), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("channel IN ('in_app', 'email')", name="ck_feedback_notifications_channel"),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'sent', 'failed', 'skipped')",
            name="ck_feedback_notifications_status",
        ),
        sa.CheckConstraint(
            "generated_by IN ('ai', 'staff', 'system')",
            name="ck_feedback_notifications_generated_by",
        ),
    )
    op.create_index(
        "ix_feedback_notifications_user_created",
        "feedback_notifications",
        ["org_id", "user_id", "created_at"],
    )
    op.create_index("ix_feedback_notifications_item", "feedback_notifications", ["item_id"])

    _replace_feedback_tenant_read_policies()
    _enable_notification_rls()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_notifications_select ON feedback_notifications")
    op.execute("DROP POLICY IF EXISTS feedback_notifications_insert ON feedback_notifications")
    op.execute("DROP POLICY IF EXISTS feedback_notifications_update ON feedback_notifications")
    op.execute("DROP POLICY IF EXISTS feedback_notifications_delete ON feedback_notifications")
    op.drop_index("ix_feedback_notifications_item", table_name="feedback_notifications")
    op.drop_index("ix_feedback_notifications_user_created", table_name="feedback_notifications")
    op.drop_table("feedback_notifications")

    op.execute("DROP POLICY IF EXISTS feedback_items_select ON feedback_items")
    op.execute("""
        CREATE POLICY feedback_items_select ON feedback_items
            FOR SELECT
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("DROP POLICY IF EXISTS feedback_item_links_select ON feedback_item_links")
    op.execute("""
        CREATE POLICY feedback_item_links_select ON feedback_item_links
            FOR SELECT
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)

    op.drop_column("feedback_items", "notification_state")
    op.drop_column("feedback_items", "resolved_by")
    op.drop_column("feedback_items", "resolved_at")
    op.drop_column("feedback_items", "resolution_summary")
    op.drop_constraint("ck_feedback_items_status", "feedback_items", type_="check")
    op.create_check_constraint(
        "ck_feedback_items_status",
        "feedback_items",
        "status IN ('inbox', 'under_review', 'planned', 'in_progress', 'shipped', 'wont_do')",
    )


def _replace_feedback_tenant_read_policies() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_item_links_select ON feedback_item_links")
    op.execute("""
        CREATE POLICY feedback_item_links_select ON feedback_item_links
            FOR SELECT
            USING (
                current_setting('app.cross_org_admin', true) = 'true'
                OR EXISTS (
                    SELECT 1
                    FROM feedback_submissions fs
                    WHERE fs.id = feedback_item_links.submission_id
                      AND fs.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                      AND fs.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
                )
            )
    """)
    op.execute("DROP POLICY IF EXISTS feedback_items_select ON feedback_items")
    op.execute("""
        CREATE POLICY feedback_items_select ON feedback_items
            FOR SELECT
            USING (
                current_setting('app.cross_org_admin', true) = 'true'
                OR EXISTS (
                    SELECT 1
                    FROM feedback_item_links fil
                    JOIN feedback_submissions fs ON fs.id = fil.submission_id
                    WHERE fil.item_id = feedback_items.id
                      AND fs.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                      AND fs.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
                )
            )
    """)


def _enable_notification_rls() -> None:
    op.execute("ALTER TABLE feedback_notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_notifications FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY feedback_notifications_select ON feedback_notifications
            FOR SELECT
            USING (
                current_setting('app.cross_org_admin', true) = 'true'
                OR (
                    org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                    AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
                )
            )
    """)
    op.execute("""
        CREATE POLICY feedback_notifications_insert ON feedback_notifications
            FOR INSERT
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_notifications_update ON feedback_notifications
            FOR UPDATE
            USING (
                current_setting('app.cross_org_admin', true) = 'true'
                OR (
                    org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                    AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
                )
            )
            WITH CHECK (
                current_setting('app.cross_org_admin', true) = 'true'
                OR (
                    org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                    AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
                )
            )
    """)
    op.execute("""
        CREATE POLICY feedback_notifications_delete ON feedback_notifications
            FOR DELETE
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
