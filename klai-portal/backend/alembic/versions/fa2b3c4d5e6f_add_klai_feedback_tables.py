"""add Klai feedback persistence tables

Revision ID: fa2b3c4d5e6f
Revises: c9d8e7f6a5b4
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "fa2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_submissions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'new'")),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("route_id", sa.String(length=512), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("viewport", sa.String(length=32), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("referrer", sa.String(length=2048), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source IN ('assistant_feedback', 'assistant_problem', 'assistant_question', 'chat_rating', 'manual_import')",
            name="ck_feedback_submissions_source",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'triage_suggested', 'linked', 'dismissed', 'support')",
            name="ck_feedback_submissions_status",
        ),
    )
    op.create_index("ix_feedback_submissions_org_created", "feedback_submissions", ["org_id", "created_at"])
    op.create_index("ix_feedback_submissions_source_created", "feedback_submissions", ["source", "created_at"])
    op.create_index("ix_feedback_submissions_status_created", "feedback_submissions", ["status", "created_at"])

    op.create_table(
        "feedback_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'inbox'")),
        sa.Column("area", sa.String(length=128), nullable=True),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("org_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_tracker_type", sa.String(length=32), nullable=True),
        sa.Column("external_tracker_id", sa.String(length=128), nullable=True),
        sa.Column("external_tracker_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ('feature', 'bug', 'ux_confusion', 'docs', 'support_pattern')",
            name="ck_feedback_items_kind",
        ),
        sa.CheckConstraint(
            "status IN ('inbox', 'under_review', 'planned', 'in_progress', 'shipped', 'wont_do')",
            name="ck_feedback_items_status",
        ),
    )
    op.create_index("ix_feedback_items_status_updated", "feedback_items", ["status", "updated_at"])
    op.create_index("ix_feedback_items_area_status", "feedback_items", ["area", "status"])

    op.create_table(
        "feedback_item_links",
        sa.Column("item_id", sa.BigInteger(), sa.ForeignKey("feedback_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "submission_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_submissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("link_type", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "link_type IN ('upvote', 'evidence', 'bug_repro', 'support_signal')",
            name="ck_feedback_item_links_link_type",
        ),
        sa.CheckConstraint("created_by IN ('ai', 'staff')", name="ck_feedback_item_links_created_by"),
    )
    op.create_index("ix_feedback_item_links_submission", "feedback_item_links", ["submission_id"])

    op.create_table(
        "feedback_triage_suggestions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            sa.BigInteger(),
            sa.ForeignKey("feedback_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("classification", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("suggested_area", sa.String(length=128), nullable=True),
        sa.Column("suggested_severity", sa.String(length=32), nullable=True),
        sa.Column(
            "duplicate_candidates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("suggested_action", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_feedback_triage_suggestions_submission",
        "feedback_triage_suggestions",
        ["submission_id"],
    )
    op.create_index("ix_feedback_triage_suggestions_created", "feedback_triage_suggestions", ["created_at"])

    _enable_rls()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_triage_suggestions_select ON feedback_triage_suggestions")
    op.execute("DROP POLICY IF EXISTS feedback_triage_suggestions_insert ON feedback_triage_suggestions")
    op.execute("DROP POLICY IF EXISTS feedback_triage_suggestions_update ON feedback_triage_suggestions")
    op.execute("DROP POLICY IF EXISTS feedback_triage_suggestions_delete ON feedback_triage_suggestions")
    op.execute("DROP POLICY IF EXISTS feedback_item_links_select ON feedback_item_links")
    op.execute("DROP POLICY IF EXISTS feedback_item_links_insert ON feedback_item_links")
    op.execute("DROP POLICY IF EXISTS feedback_item_links_update ON feedback_item_links")
    op.execute("DROP POLICY IF EXISTS feedback_item_links_delete ON feedback_item_links")
    op.execute("DROP POLICY IF EXISTS feedback_items_select ON feedback_items")
    op.execute("DROP POLICY IF EXISTS feedback_items_insert ON feedback_items")
    op.execute("DROP POLICY IF EXISTS feedback_items_update ON feedback_items")
    op.execute("DROP POLICY IF EXISTS feedback_items_delete ON feedback_items")
    op.execute("DROP POLICY IF EXISTS feedback_submissions_select ON feedback_submissions")
    op.execute("DROP POLICY IF EXISTS feedback_submissions_insert ON feedback_submissions")
    op.execute("DROP POLICY IF EXISTS feedback_submissions_update ON feedback_submissions")

    op.drop_index("ix_feedback_triage_suggestions_created", table_name="feedback_triage_suggestions")
    op.drop_index("ix_feedback_triage_suggestions_submission", table_name="feedback_triage_suggestions")
    op.drop_table("feedback_triage_suggestions")
    op.drop_index("ix_feedback_item_links_submission", table_name="feedback_item_links")
    op.drop_table("feedback_item_links")
    op.drop_index("ix_feedback_items_area_status", table_name="feedback_items")
    op.drop_index("ix_feedback_items_status_updated", table_name="feedback_items")
    op.drop_table("feedback_items")
    op.drop_index("ix_feedback_submissions_status_created", table_name="feedback_submissions")
    op.drop_index("ix_feedback_submissions_source_created", table_name="feedback_submissions")
    op.drop_index("ix_feedback_submissions_org_created", table_name="feedback_submissions")
    op.drop_table("feedback_submissions")


def _enable_rls() -> None:
    op.execute("ALTER TABLE feedback_submissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_submissions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY feedback_submissions_select ON feedback_submissions
            FOR SELECT
            USING (
                current_setting('app.cross_org_admin', true) = 'true'
                OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            )
    """)
    op.execute("""
        CREATE POLICY feedback_submissions_insert ON feedback_submissions
            FOR INSERT
            WITH CHECK (
                org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            )
    """)
    op.execute("""
        CREATE POLICY feedback_submissions_update ON feedback_submissions
            FOR UPDATE
            USING (current_setting('app.cross_org_admin', true) = 'true')
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)

    op.execute("ALTER TABLE feedback_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_items FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY feedback_items_select ON feedback_items
            FOR SELECT
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_items_insert ON feedback_items
            FOR INSERT
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_items_update ON feedback_items
            FOR UPDATE
            USING (current_setting('app.cross_org_admin', true) = 'true')
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_items_delete ON feedback_items
            FOR DELETE
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)

    op.execute("ALTER TABLE feedback_item_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_item_links FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY feedback_item_links_select ON feedback_item_links
            FOR SELECT
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_item_links_insert ON feedback_item_links
            FOR INSERT
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_item_links_update ON feedback_item_links
            FOR UPDATE
            USING (current_setting('app.cross_org_admin', true) = 'true')
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_item_links_delete ON feedback_item_links
            FOR DELETE
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)

    op.execute("ALTER TABLE feedback_triage_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feedback_triage_suggestions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY feedback_triage_suggestions_select ON feedback_triage_suggestions
            FOR SELECT
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_triage_suggestions_insert ON feedback_triage_suggestions
            FOR INSERT
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_triage_suggestions_update ON feedback_triage_suggestions
            FOR UPDATE
            USING (current_setting('app.cross_org_admin', true) = 'true')
            WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY feedback_triage_suggestions_delete ON feedback_triage_suggestions
            FOR DELETE
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)
