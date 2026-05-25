"""REQ-18 — DB-level safe-slug check constraint on portal_orgs.slug.

Pairs with app/services/provisioning/_slug_guard.py::_assert_safe_slug —
identical regex enforced at the row level so a row with a malformed slug
cannot exist regardless of which code-path tries to insert it.

portal_orgs is portal_api-owned (auth/identity table), so this DDL runs
inside ``op.execute`` in ``upgrade()`` per Klai alembic conventions
(`alembic-cannot-drop-non-portal_api-tables` pitfall — portal_api-owned
tables CAN run normal alembic upgrade).

Revision ID: 45b528904319
Revises: 5c6ad9cf7983
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "45b528904319"
down_revision = "5c6ad9cf7983"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add CHECK constraint chk_portal_orgs_slug_safe on portal_orgs.slug.

    Idempotent via DO-block guard so a manual pre-deploy DDL run does not
    block alembic upgrade head.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'chk_portal_orgs_slug_safe'
                  AND conrelid = 'portal_orgs'::regclass
            ) THEN
                ALTER TABLE portal_orgs
                    ADD CONSTRAINT chk_portal_orgs_slug_safe
                    CHECK (slug ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$');
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop CHECK constraint chk_portal_orgs_slug_safe."""
    op.execute("ALTER TABLE portal_orgs DROP CONSTRAINT IF EXISTS chk_portal_orgs_slug_safe;")
