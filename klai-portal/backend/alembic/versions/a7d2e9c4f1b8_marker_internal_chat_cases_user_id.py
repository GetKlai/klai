"""internal_chat_cases.user_id — marker.

The case an internal conversation carries to its next turn is stored with the
employee it belongs to, so offboarding and user deletion can delete it
(``widget_audit.forget_internal_cases``).

``internal_chat_cases`` is handed to ``klai`` by
``post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql``, so portal_api cannot
ALTER it; the column and its index are added as the klai superuser by
``post_deploy_a7d2e9c4f1b8_internal_chat_cases_user_id.sql``. This file exists
only so alembic can advance its head past f3a9c6e2b4d1.

Revision ID: a7d2e9c4f1b8
Revises: f3a9c6e2b4d1
Create Date: 2026-10-22
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "a7d2e9c4f1b8"
down_revision: str | None = "f3a9c6e2b4d1"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling post_deploy SQL."""


def downgrade() -> None:
    """No-op marker. To roll back, as klai: DROP INDEX IF EXISTS ix_internal_chat_cases_org_user;
    ALTER TABLE internal_chat_cases DROP COLUMN IF EXISTS user_id."""
