"""scrub raw text from Klai assistant feedback product events

Revision ID: fb2c3d4e5f6a
Revises: fa2b3c4d5e6f
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op


revision: str = "fb2c3d4e5f6a"
down_revision: Union[str, Sequence[str], None] = "fa2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE product_events
        SET properties = properties - 'raw_text'
        WHERE event_type IN ('klai_assistant.feedback', 'klai_assistant.problem_report')
          AND properties ? 'raw_text'
        """
    )


def downgrade() -> None:
    # Raw text cannot be restored once scrubbed.
    pass
