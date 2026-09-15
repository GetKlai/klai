"""add resolved_by + resolved_by_user_id to portal_retrieval_gaps

Revision ID: 997e0b66f750
Revises: d8b3f6a1c4e9
Create Date: 2026-09-15

SPEC-KNOWLEDGE-ACTIVITY-001 §4.5/§4.9: a gap only recorded when it closed, not
who closed it. Three closers exist (gap_rescorer, the answer-review flow in
app_activity.py, and the manual POST /api/app/gaps/resolve) and each now
stamps its own row.

Columns only -- the FOREIGN KEY on portal_users is NOT created here:
portal_api (the role alembic runs as) has no REFERENCES privilege on that
klai-owned table (same restriction documented in
c2a7e9d4b1f6_marker_answer_reviews.py for reviewer_user_id, and in
d8b3f6a1c4e9_gaps_conversation_language.py for conversation_id). It ships in
post_deploy_997e0b66f750_gaps_resolved_by_fk.sql, applied as klai superuser.
``portal_retrieval_gaps`` itself is portal_api-owned, so the plain ADD
COLUMNs and the single-table CHECK constraint below are safe; both columns
are nullable, so no backfill is needed.
"""

from alembic import op
import sqlalchemy as sa

revision = "997e0b66f750"
down_revision = "d8b3f6a1c4e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_retrieval_gaps", sa.Column("resolved_by", sa.String(length=16), nullable=True))
    op.add_column("portal_retrieval_gaps", sa.Column("resolved_by_user_id", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_retrieval_gaps_resolved_by",
        "portal_retrieval_gaps",
        "resolved_by IN ('rescorer', 'review', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_retrieval_gaps_resolved_by", "portal_retrieval_gaps", type_="check")
    op.drop_column("portal_retrieval_gaps", "resolved_by_user_id")
    op.drop_column("portal_retrieval_gaps", "resolved_by")
