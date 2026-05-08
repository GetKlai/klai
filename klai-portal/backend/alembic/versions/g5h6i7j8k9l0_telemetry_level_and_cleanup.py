"""SPEC-PRIVACY-QUERY-SHADOW-001: portal_orgs.telemetry_level + legacy gap cleanup

Revision ID: g5h6i7j8k9l0
Revises: f6d914f040da
Create Date: 2026-05-08

Adds the per-tenant telemetry-level column and runs the one-time hard
cleanup of pre-existing raw query text in portal_retrieval_gaps. The
companion post_deploy SQL (post_deploy_g5h6i7j8k9l0.sql) creates the
`telemetry` schema and `telemetry.query_shadow` table — those statements
require the `klai` superuser because portal_api cannot CREATE SCHEMA.

Three statements run via standard alembic (portal_api role owns
portal_orgs and portal_retrieval_gaps):

1. CREATE TYPE telemetry_level_t (idempotent via DO-block)
2. ALTER TABLE portal_orgs ADD COLUMN telemetry_level (default 'shadow')
3. One-time cleanup on portal_retrieval_gaps:
   - UPDATE rows older than 7d to '[REDACTED:legacy]' (privacy debt closure)
   - DELETE rows older than 30d (existing legacy purge)

The cleanup is idempotent — re-runs are no-ops because the WHERE clause
matches only non-redacted rows. After this migration ships, the daily
TTL job (Unit 7) maintains the invariant.

Pre-flight verified 2026-05-08: portal_retrieval_gaps row count is 0 on
prod, so the cleanup blast radius is zero. The migration is safe to run.

REQ-1, REQ-12.
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

    # 3. One-time legacy cleanup (REQ-12). Idempotent — the WHERE clause
    #    excludes already-redacted rows, so re-runs touch nothing.
    op.execute(
        """
        UPDATE public.portal_retrieval_gaps
           SET query_text = '[REDACTED:legacy]'
         WHERE query_text NOT LIKE '[REDACTED:%'
           AND occurred_at < now() - interval '7 days';
        """
    )
    op.execute(
        """
        DELETE FROM public.portal_retrieval_gaps
         WHERE occurred_at < now() - interval '30 days';
        """
    )


def downgrade() -> None:
    # The cleanup UPDATE/DELETE cannot be reversed (data is gone). Only the
    # column + type drops are downgradable. Documented in the SPEC's
    # Rollback section: a privacy-regression rollback flips the per-tenant
    # column back to 'full' on affected tenants instead of dropping the
    # column.
    op.execute("ALTER TABLE public.portal_orgs DROP COLUMN IF EXISTS telemetry_level;")
    op.execute("DROP TYPE IF EXISTS telemetry_level_t;")
