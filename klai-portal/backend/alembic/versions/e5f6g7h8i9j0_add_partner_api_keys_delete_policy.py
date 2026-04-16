"""add DELETE policy on partner_api_keys

Fixes silent RLS fail: partner_api_keys had RLS enabled and split SELECT/INSERT/UPDATE
policies, but no DELETE policy. DELETE statements from portal_api were silently rejected
(0 rows affected, no error) because PostgreSQL RLS denies by default when no matching
policy exists.

Note: This migration uses DROP + CREATE for idempotency because CREATE POLICY IF NOT
EXISTS is not supported in PostgreSQL. In production, this policy was already created
manually as klai superuser; this migration exists for code history and for any fresh
environment to reproduce the state.

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-04-16
"""

from alembic import op

revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop any existing DELETE policy (idempotent) then recreate
    op.execute("DROP POLICY IF EXISTS partner_delete ON partner_api_keys")
    op.execute(
        """
        CREATE POLICY partner_delete ON partner_api_keys
            FOR DELETE TO portal_api
            USING (org_id = current_setting('app.current_org_id', true)::integer)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS partner_delete ON partner_api_keys")
