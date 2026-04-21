"""add DELETE policy on partner_api_keys

Fixes silent RLS fail: partner_api_keys had RLS enabled and split
SELECT/INSERT/UPDATE policies for portal_api, but no DELETE policy.
PostgreSQL RLS denies by default when no matching policy exists — so
DELETE /api/integrations/:id silently affected 0 rows and returned 204.

NOTE: partner_api_keys is owned by the klai superuser, not by the alembic
migration role. CREATE/DROP POLICY on this table can only run as owner,
so this migration wraps the DDL in a PL/pgSQL DO block that traps
insufficient_privilege and continues. The policy itself is applied
manually in every environment as klai superuser (see klai-infra RLS docs).

This migration primarily exists for code history and to keep alembic_version
in sync with the intended schema state.

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-04-16
"""

from alembic import op

revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
DO $$
BEGIN
    DROP POLICY IF EXISTS partner_delete ON partner_api_keys;
    CREATE POLICY partner_delete ON partner_api_keys
        FOR DELETE TO portal_api
        USING (org_id = current_setting('app.current_org_id', true)::integer);
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping partner_delete policy: migration role is not the owner of partner_api_keys. Apply manually as klai superuser.';
END
$$;
"""


DOWNGRADE_SQL = """
DO $$
BEGIN
    DROP POLICY IF EXISTS partner_delete ON partner_api_keys;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping partner_delete policy drop: migration role is not the owner.';
END
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
