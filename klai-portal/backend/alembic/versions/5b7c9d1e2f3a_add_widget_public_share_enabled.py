"""add widget public share flag column

Revision ID: 5b7c9d1e2f3a
Revises: 34d8f876ffbf
Create Date: 2026-05-24

Move the public share-link toggle out of ``widgets.widget_config`` JSONB
and into a first-class column. The flag controls unauthenticated public
bot-link access, so it should be explicit in schema, defaults, migrations,
and audit/review surfaces instead of being hidden in flexible JSON.

Production note: ``widgets`` is owned by the ``klai`` superuser and has
FORCE RLS enabled. Alembic runs as ``portal_api``, so the production DDL and
cross-tenant backfill live in
``post_deploy_5b7c9d1e2f3a_widget_public_share_enabled.sql``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "5b7c9d1e2f3a"
down_revision: str | None = "34d8f876ffbf"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    conn = op.get_bind()
    owner = conn.execute(
        sa.text(
            """
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename = 'widgets'
            """
        )
    ).scalar_one_or_none()
    current_user = conn.execute(sa.text("SELECT current_user")).scalar_one()

    if owner != current_user:
        conn.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    RAISE NOTICE 'Skipping widgets.public_share_enabled migration. Apply post_deploy_5b7c9d1e2f3a_widget_public_share_enabled.sql as klai.';
                END
                $$;
                """
            )
        )
        return

    conn.execute(sa.text("SET LOCAL app.cross_org_admin = TRUE"))
    conn.execute(
        sa.text(
            """
            ALTER TABLE widgets
            ADD COLUMN IF NOT EXISTS public_share_enabled boolean DEFAULT false NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE widgets
            SET public_share_enabled = COALESCE((widget_config ->> 'public_share_enabled')::boolean, false)
            WHERE widget_config ? 'public_share_enabled'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE widgets
            SET widget_config = widget_config - 'public_share_enabled'
            WHERE widget_config ? 'public_share_enabled'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    owner = conn.execute(
        sa.text(
            """
            SELECT tableowner
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename = 'widgets'
            """
        )
    ).scalar_one_or_none()
    current_user = conn.execute(sa.text("SELECT current_user")).scalar_one()

    if owner != current_user:
        conn.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    RAISE NOTICE 'Skipping widgets.public_share_enabled downgrade. Apply downgrade manually as table owner.';
                END
                $$;
                """
            )
        )
        return

    conn.execute(sa.text("SET LOCAL app.cross_org_admin = TRUE"))
    conn.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'widgets'
                      AND column_name = 'public_share_enabled'
                ) THEN
                    UPDATE widgets
                    SET widget_config = jsonb_set(
                        COALESCE(widget_config, '{}'::jsonb),
                        '{public_share_enabled}',
                        to_jsonb(public_share_enabled),
                        true
                    );
                END IF;
            END
            $$;
            """
        )
    )
    conn.execute(sa.text("ALTER TABLE widgets DROP COLUMN IF EXISTS public_share_enabled"))
