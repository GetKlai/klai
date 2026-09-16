"""widen portal_retrieval_gaps.resolved_by CHECK to add 'test'

Revision ID: 6a0a2f1c33d6
Revises: 2753d8303a76
Create Date: 2026-09-16

SPEC-KNOWLEDGE-ACTIVITY-001: marking a conversation as a test message
(PUT /api/app/activity/conversations/{id}/test) resolves its open gaps the
same way the rescorer/review/manual closers already do, so it needs its own
``resolved_by`` value to tell "closed because it was a test message" apart
from "closed because someone reviewed it" or "closed by hand" — reversible on
unmark, which only reopens the rows this closer stamped.

``portal_retrieval_gaps`` is portal_api-owned (see
997e0b66f750_add_resolved_by_to_retrieval_gaps.py), so the plain
drop/create CHECK constraint below is safe to run as portal_api; no FK
involved, no post_deploy SQL needed.
"""

from alembic import op

revision = "6a0a2f1c33d6"
down_revision = "2753d8303a76"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_retrieval_gaps_resolved_by", "portal_retrieval_gaps", type_="check")
    op.create_check_constraint(
        "ck_retrieval_gaps_resolved_by",
        "portal_retrieval_gaps",
        "resolved_by IN ('rescorer', 'review', 'manual', 'test')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_retrieval_gaps_resolved_by", "portal_retrieval_gaps", type_="check")
    op.create_check_constraint(
        "ck_retrieval_gaps_resolved_by",
        "portal_retrieval_gaps",
        "resolved_by IN ('rescorer', 'review', 'manual')",
    )
