"""drop portal_gap_kept_questions (marker revision)

The table held the question of a widget turn that counts as a knowledge gap
for four weeks, because widget messages were purged after 7 days while a gap
group can stay open for four weeks. Widget messages now live for the
organisation's retention term (90 days by default), so the knowledge-gap list
reads every turn of its window from the messages and nothing reads or writes
this table since #2352 (docs/architecture/conversation-retention-and-learning.md).

The drop is not done here: in production the table is owned by ``klai``, and
Alembic runs as ``portal_api``. It lives in
``post_deploy_d7b4e1a9c3f6_drop_gap_kept_questions.sql``, applied as ``klai``.
It ships on its own, after the release that stopped using the table, because
an automatic rollback puts the previous image back on the newer schema
(SPEC-INFRA-AUTO-ROLLBACK-001 R3).
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "d7b4e1a9c3f6"
down_revision: str | None = "c8e2f5a1d3b7"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: see the module docstring; the DROP runs post-deploy as klai.
    pass


def downgrade() -> None:
    pass
