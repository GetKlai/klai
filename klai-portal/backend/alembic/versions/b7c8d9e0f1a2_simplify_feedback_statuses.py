"""simplify feedback statuses

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-05-28
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("LOCK TABLE feedback_submissions, feedback_items IN ACCESS EXCLUSIVE MODE")

    op.drop_constraint("ck_feedback_submissions_status", "feedback_submissions", type_="check")
    op.drop_constraint("ck_feedback_items_status", "feedback_items", type_="check")

    op.execute("""
        UPDATE feedback_items
        SET status = CASE
            WHEN status IN ('resolved', 'shipped') THEN 'resolved'
            WHEN status = 'wont_do' THEN 'dismissed'
            ELSE 'open'
        END
    """)
    op.execute("""
        UPDATE feedback_submissions AS fs
        SET status = 'resolved'
        FROM feedback_item_links AS fil
        JOIN feedback_items AS fi ON fi.id = fil.item_id
        WHERE fs.id = fil.submission_id
          AND fs.status = 'linked'
          AND fi.status = 'resolved'
    """)
    op.execute("""
        UPDATE feedback_submissions
        SET status = CASE
            WHEN status = 'new' THEN 'new'
            WHEN status = 'triage_suggested' THEN 'new'
            WHEN status IN ('resolved', 'shipped') THEN 'resolved'
            WHEN status IN ('dismissed', 'wont_do') THEN 'dismissed'
            WHEN status = 'support' THEN 'support'
            WHEN status = 'linked' THEN 'open'
            ELSE 'open'
        END
    """)

    op.create_check_constraint(
        "ck_feedback_submissions_status",
        "feedback_submissions",
        "status IN ('new', 'open', 'resolved', 'dismissed', 'support')",
    )
    op.create_check_constraint(
        "ck_feedback_items_status",
        "feedback_items",
        "status IN ('open', 'resolved', 'dismissed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_feedback_submissions_status", "feedback_submissions", type_="check")
    op.drop_constraint("ck_feedback_items_status", "feedback_items", type_="check")

    op.execute("""
        UPDATE feedback_submissions
        SET status = CASE
            WHEN status = 'open' THEN 'linked'
            WHEN status = 'resolved' THEN 'linked'
            ELSE status
        END
    """)
    op.execute("""
        UPDATE feedback_items
        SET status = CASE
            WHEN status = 'open' THEN 'inbox'
            WHEN status = 'dismissed' THEN 'wont_do'
            ELSE status
        END
    """)

    op.create_check_constraint(
        "ck_feedback_submissions_status",
        "feedback_submissions",
        "status IN ('new', 'triage_suggested', 'linked', 'dismissed', 'support')",
    )
    op.create_check_constraint(
        "ck_feedback_items_status",
        "feedback_items",
        "status IN ('inbox', 'under_review', 'planned', 'in_progress', 'shipped', 'resolved', 'wont_do')",
    )
