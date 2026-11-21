"""portal_gap_kept_questions (a widget gap question kept past the message retention; table since removed)

This revision created ``portal_gap_kept_questions`` on 2026-11-11: the question
of a counted widget gap turn was kept 28 days while the messages were purged
after seven. Widget messages now live for the organisation's retention term,
nothing uses the table, and revision ``d7b4e1a9c3f6`` dropped it
(docs/architecture/conversation-retention-and-learning.md). The revision stays
as a link in the chain, and its upgrade is a no-op so that a fresh database
does not create a table only for the next revision's post-deploy script to
drop it again.

Revision ID: c8e2f5a1d3b7
Revises: sl4c5k6i7n8s
Create Date: 2026-11-11
"""

from __future__ import annotations

revision: str = "c8e2f5a1d3b7"
down_revision: str | None = "sl4c5k6i7n8s"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
