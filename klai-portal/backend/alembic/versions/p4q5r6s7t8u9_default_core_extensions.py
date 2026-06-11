"""Default partner_api, scribe, and widgets on for all tenants.

Revision ID: p4q5r6s7t8u9
Revises: 0aac04f1bccc
Create Date: 2026-06-11
"""

from __future__ import annotations

from alembic import op

revision = "p4q5r6s7t8u9"
down_revision = "0aac04f1bccc"
branch_labels = None
depends_on = None


DEFAULT_FEATURES_SQL = "ARRAY['partner_api', 'scribe', 'widgets']::text[]"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE portal_orgs
        ALTER COLUMN platform_unlocked_features SET DEFAULT {DEFAULT_FEATURES_SQL}
        """
    )
    op.execute(
        f"""
        UPDATE portal_orgs
        SET platform_unlocked_features = (
            SELECT COALESCE(array_agg(DISTINCT feature ORDER BY feature), '{{}}'::text[])
            FROM unnest(COALESCE(platform_unlocked_features, '{{}}'::text[]) || {DEFAULT_FEATURES_SQL}) AS feature
        )
        WHERE NOT (
            COALESCE(platform_unlocked_features, '{{}}'::text[]) @> {DEFAULT_FEATURES_SQL}
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION portal_orgs_apply_default_platform_features()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.platform_unlocked_features := (
                SELECT COALESCE(array_agg(DISTINCT feature ORDER BY feature), {DEFAULT_FEATURES_SQL})
                FROM unnest(COALESCE(NEW.platform_unlocked_features, '{{}}'::text[]) || {DEFAULT_FEATURES_SQL}) AS feature
            );
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS portal_orgs_default_platform_features_insert ON portal_orgs")
    op.execute(
        """
        CREATE TRIGGER portal_orgs_default_platform_features_insert
        BEFORE INSERT ON portal_orgs
        FOR EACH ROW
        EXECUTE FUNCTION portal_orgs_apply_default_platform_features()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS portal_orgs_default_platform_features_insert ON portal_orgs")
    op.execute("DROP FUNCTION IF EXISTS portal_orgs_apply_default_platform_features()")
    op.execute(
        """
        ALTER TABLE portal_orgs
        ALTER COLUMN platform_unlocked_features SET DEFAULT '{}'::text[]
        """
    )
