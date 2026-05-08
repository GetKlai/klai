"""SPEC-PRIVACY-QUERY-SHADOW-001: portal_orgs.telemetry_level

Revision ID: g5h6i7j8k9l0
Revises: f6d914f040da
Create Date: 2026-05-08

Adds the per-tenant telemetry-level enum + column on portal_orgs.

The legacy data cleanup (REQ-12) and the telemetry schema/table (REQ-7)
both live in the companion post_deploy_g5h6i7j8k9l0.sql because:

- ``portal_retrieval_gaps`` is RLS-protected (Category-D, strict). UPDATE
  / DELETE statements from alembic (which runs as the ``portal_api`` role
  without ``app.current_org_id`` set) trip the strict policy and raise
  ``InsufficientPrivilegeError`` (42501). The post-deploy SQL runs as
  the ``klai`` superuser which bypasses RLS.
- The ``telemetry`` schema and ``CREATE EXTENSION vector`` need
  superuser privileges that ``portal_api`` does not have.

Two statements run via standard alembic (portal_api owns the type catalog
+ portal_orgs):

1. CREATE TYPE telemetry_level_t (idempotent via DO-block)
2. ALTER TABLE portal_orgs ADD COLUMN telemetry_level (default 'shadow')

Pre-flight verified 2026-05-08: portal_retrieval_gaps row count is 0 on
prod, so the cleanup blast radius is zero. The migration is safe to run.

REQ-1.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "g5h6i7j8k9l0"
down_revision = "f6d914f040da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Enum type for the per-tenant telemetry level.
    #    DO-block makes CREATE TYPE idempotent (PostgreSQL has no IF NOT EXISTS for types).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE telemetry_level_t AS ENUM ('off', 'shadow', 'full');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    # 2. Per-tenant column, default 'shadow' so existing tenants migrate
    #    to the privacy-friendly default automatically (REQ-1).
    op.execute(
        """
        ALTER TABLE public.portal_orgs
            ADD COLUMN IF NOT EXISTS telemetry_level telemetry_level_t
                NOT NULL DEFAULT 'shadow';
        """
    )


def downgrade() -> None:
    # Documented in the SPEC's Rollback section: a privacy-regression
    # rollback flips the per-tenant column back to 'full' on affected
    # tenants instead of dropping the column.
    op.execute("ALTER TABLE public.portal_orgs DROP COLUMN IF EXISTS telemetry_level;")
    op.execute("DROP TYPE IF EXISTS telemetry_level_t;")
