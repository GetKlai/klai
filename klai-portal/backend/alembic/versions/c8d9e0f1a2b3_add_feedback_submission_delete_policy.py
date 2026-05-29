"""add feedback submission delete policy

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_submissions_delete ON feedback_submissions")
    op.execute("""
        CREATE POLICY feedback_submissions_delete ON feedback_submissions
            FOR DELETE
            USING (current_setting('app.cross_org_admin', true) = 'true')
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feedback_submissions_delete ON feedback_submissions")
