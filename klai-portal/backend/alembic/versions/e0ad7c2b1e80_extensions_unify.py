"""SPEC-PORTAL-EXTENSIONS-UNIFY-001: drop enabled_addons, unify on platform_unlocked_features.

Two parallel gating columns (enabled_addons tenant-self-service +
platform_unlocked_features Klai-staff) collapse into one. Done in three
atomic UPDATE+DDL steps:

1. Set-union copy: for every org with non-empty enabled_addons, add those
   entries to platform_unlocked_features (idempotent via DISTINCT).
2. Voys explicit unlock (per product owner 2026-05-12): all five features
   enabled. Hard-coded in the migration so the data-decision is auditable
   in commit history rather than via a post-deploy curl.
3. DROP COLUMN portal_orgs.enabled_addons. portal_api owns the table on
   prod (verified 2026-05-12 via pg_tables.tableowner), so the standard
   alembic upgrade path can execute the ALTER TABLE without escalation.

Single transaction — if any step fails, the whole migration rolls back
and the column survives. Container entrypoint.sh re-attempts on next start
unless the failure is wedged (then manual psql + alembic stamp recovery
per .claude/rules/klai/pitfalls/process-rules.md::alembic-cannot-drop-non-portal_api-tables).

Revision: e0ad7c2b1e80
Down-revision: e3765cd03dd2
Created: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e0ad7c2b1e80"
down_revision = "e3765cd03dd2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: set-union copy. Any tenant with a non-empty enabled_addons
    # gets those values merged (de-duplicated, sorted) into
    # platform_unlocked_features.
    op.execute(
        """
        UPDATE portal_orgs
        SET platform_unlocked_features = (
            SELECT COALESCE(array_agg(DISTINCT x ORDER BY x), '{}'::text[])
            FROM unnest(
                COALESCE(platform_unlocked_features, '{}'::text[])
                || COALESCE(enabled_addons, '{}'::text[])
            ) AS x
        )
        WHERE enabled_addons IS NOT NULL
          AND cardinality(enabled_addons) > 0
        """
    )

    # Step 2: per product owner decision (sparring 2026-05-12) — Voys
    # gets every extension unlocked. Hard-coded here so the data-decision
    # lives in commit history, not in an out-of-band curl.
    op.execute(
        """
        UPDATE portal_orgs
        SET platform_unlocked_features = ARRAY[
            'custom_mcps', 'docs', 'partner_api', 'scribe', 'widgets'
        ]::text[]
        WHERE slug = 'voys'
        """
    )

    # Step 3: drop the now-redundant column. portal_api owns portal_orgs on
    # prod (verified 2026-05-12 via pg_tables.tableowner).
    op.drop_column("portal_orgs", "enabled_addons")


def downgrade() -> None:
    # Best-effort rollback: re-create the column with empty array default.
    # Original per-tenant enabled_addons values are NOT restorable — they
    # were merged into platform_unlocked_features by upgrade Step 1, and
    # there is no way to split them back out after the union. Acceptable
    # because this SPEC retires enabled_addons as a concept; rollback
    # would only be invoked if downstream code references the column
    # again, in which case re-deriving the value from the application
    # layer is the operator's problem.
    op.add_column(
        "portal_orgs",
        sa.Column(
            "enabled_addons",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
