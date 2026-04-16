"""split partner_api_keys into api keys and widgets — SPEC-WIDGET-002

Creates a separate `widgets` table for chat-widget integrations, moves all
`integration_type='widget'` rows from `partner_api_keys` into it, and drops
the widget-specific and soft-delete columns from `partner_api_keys`.

After this migration:
  - `partner_api_keys` holds only developer-facing API keys
    (`pk_live_...`), no widget columns, no `active` column.
  - `widgets` holds chat widget integrations, with no authentication-secret
    columns (no `key_prefix`, no `key_hash`, no `permissions`) — widget auth
    is 100% JWT-based via `WIDGET_JWT_SECRET`.
  - `widget_kb_access` is a read-only junction (no `access_level` column).

RLS notes:
  - The migration runs as the alembic/portal_api role, which is not the
    owner of `partner_api_keys`. Column drops on `partner_api_keys` will
    therefore fail with `insufficient_privilege` and are wrapped in a DO
    block that traps and skips, mirroring the pattern established in
    `e5f6g7h8i9j0_add_partner_api_keys_delete_policy.py`.
  - CREATE TABLE for `widgets` and `widget_kb_access` works from any
    role with CREATE on the schema. The klai superuser must manually run
    `ALTER TABLE ... OWNER TO klai; ALTER TABLE ... ENABLE ROW LEVEL
    SECURITY; CREATE POLICY ...` for the new tables after this migration.
  - The post-deploy steps are captured in
    `klai-portal/backend/alembic/versions/post_deploy_f0a1b2c3d4e5.sql`.

Revision ID: f0a1b2c3d4e5
Revises: e5f6g7h8i9j0
Create Date: 2026-04-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f0a1b2c3d4e5"
down_revision = "e5f6g7h8i9j0"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

# DDL that may fail on partner_api_keys (not owned by migration role) is
# wrapped in a DO block that traps insufficient_privilege and continues with
# a NOTICE. The klai superuser applies the skipped steps manually.

_DROP_COLUMNS_ON_PARTNER_API_KEYS = """
DO $$
BEGIN
    ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS ck_partner_api_keys_integration_type;
    ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS uq_partner_api_keys_widget_id;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS integration_type;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS widget_id;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS widget_config;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS active;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping column drops on partner_api_keys: role is not the owner. Apply manually as klai superuser.';
END
$$;
"""


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. Create `widgets` table (no secret columns).
    op.create_table(
        "widgets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("widget_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "widget_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(
                '\'{"allowed_origins": [], "title": "", "welcome_message": "", "css_variables": {}}\'::jsonb'
            ),
        ),
        sa.Column(
            "rate_limit_rpm",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_widgets_org_id", "widgets", ["org_id"])
    op.create_index("ix_widgets_widget_id", "widgets", ["widget_id"], unique=True)

    # 2. Create `widget_kb_access` junction (read-only, no access_level column).
    op.create_table(
        "widget_kb_access",
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("widgets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "kb_id",
            sa.Integer(),
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_widget_kb_access_kb_id",
        "widget_kb_access",
        ["kb_id"],
    )

    # 3. Move widget rows from partner_api_keys to widgets (idempotent).
    #    Only copy columns that exist on the new table. key_prefix, key_hash,
    #    permissions, active are discarded — they have no analogue.
    op.execute(
        """
        INSERT INTO widgets (
            id, org_id, name, description, widget_id, widget_config,
            rate_limit_rpm, last_used_at, created_at, created_by
        )
        SELECT
            id,
            org_id,
            name,
            description,
            widget_id,
            COALESCE(widget_config, '{"allowed_origins": [], "title": "", "welcome_message": "", "css_variables": {}}'::jsonb),
            rate_limit_rpm,
            last_used_at,
            created_at,
            created_by
        FROM partner_api_keys
        WHERE integration_type = 'widget'
          AND widget_id IS NOT NULL
        ON CONFLICT (id) DO NOTHING
        """
    )

    # 4. Move associated KB access rows. Widget rows in the old junction
    #    may have access_level='read' or (rarely) 'read_write'. On widgets
    #    write access is meaningless, so all rows map to a single read-only
    #    entry in widget_kb_access. Deduplicate on insert.
    op.execute(
        """
        INSERT INTO widget_kb_access (widget_id, kb_id)
        SELECT DISTINCT kba.partner_api_key_id, kba.kb_id
        FROM partner_api_key_kb_access kba
        INNER JOIN partner_api_keys pak ON pak.id = kba.partner_api_key_id
        WHERE pak.integration_type = 'widget'
        ON CONFLICT (widget_id, kb_id) DO NOTHING
        """
    )

    # 5. Delete the old widget kb_access rows (in the API-only junction).
    op.execute(
        """
        DELETE FROM partner_api_key_kb_access
        WHERE partner_api_key_id IN (
            SELECT id FROM partner_api_keys WHERE integration_type = 'widget'
        )
        """
    )

    # 6. Delete the old widget rows from partner_api_keys.
    op.execute(
        """
        DELETE FROM partner_api_keys WHERE integration_type = 'widget'
        """
    )

    # 7. Drop widget-specific and soft-delete columns from partner_api_keys.
    #    Wrapped in a DO block because the migration role is not the table
    #    owner. Klai superuser must apply manually if the migration skips.
    op.execute(_DROP_COLUMNS_ON_PARTNER_API_KEYS)


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Downgrade is hazardous: if any new widgets were created after the
    # upgrade, they would be lost. Abort if widgets table contains rows
    # that have no matching id in partner_api_keys (i.e. created after
    # the upgrade). Idempotent otherwise.
    op.execute(
        """
        DO $$
        DECLARE
            fresh_count int;
        BEGIN
            SELECT count(*) INTO fresh_count
            FROM widgets w
            WHERE NOT EXISTS (
                SELECT 1 FROM partner_api_keys pak WHERE pak.id = w.id
            );
            IF fresh_count > 0 THEN
                RAISE EXCEPTION 'Downgrade aborted: % widget rows exist that were created after upgrade and cannot be restored. Delete them first or keep the upgrade in place.', fresh_count;
            END IF;
        END
        $$;
        """
    )

    # Re-add the dropped columns on partner_api_keys.
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
            ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS integration_type varchar(10) NOT NULL DEFAULT 'api';
            ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS widget_id varchar(64);
            ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS widget_config jsonb;
            ALTER TABLE partner_api_keys ADD CONSTRAINT IF NOT EXISTS uq_partner_api_keys_widget_id UNIQUE (widget_id);
            ALTER TABLE partner_api_keys ADD CONSTRAINT IF NOT EXISTS ck_partner_api_keys_integration_type CHECK (integration_type IN ('api', 'widget'));
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE NOTICE 'Skipping column re-add on partner_api_keys: role is not the owner. Apply manually as klai superuser.';
        END
        $$;
        """
    )

    # Copy widget rows back.
    op.execute(
        """
        INSERT INTO partner_api_keys (
            id, org_id, name, description,
            key_prefix, key_hash,
            permissions, rate_limit_rpm, active,
            last_used_at, created_at, created_by,
            integration_type, widget_id, widget_config
        )
        SELECT
            w.id,
            w.org_id,
            w.name,
            w.description,
            'wgt_restored' AS key_prefix,
            encode(digest(w.widget_id || '-downgrade-placeholder', 'sha256'), 'hex') AS key_hash,
            '{"chat": true, "feedback": false, "knowledge_append": false}'::jsonb,
            w.rate_limit_rpm,
            true,
            w.last_used_at,
            w.created_at,
            w.created_by,
            'widget',
            w.widget_id,
            w.widget_config
        FROM widgets w
        ON CONFLICT (id) DO NOTHING
        """
    )

    # Copy widget_kb_access back (access_level='read').
    op.execute(
        """
        INSERT INTO partner_api_key_kb_access (partner_api_key_id, kb_id, access_level)
        SELECT widget_id, kb_id, 'read'
        FROM widget_kb_access
        ON CONFLICT (partner_api_key_id, kb_id) DO NOTHING
        """
    )

    # Drop the new tables.
    op.drop_index("ix_widget_kb_access_kb_id", table_name="widget_kb_access")
    op.drop_table("widget_kb_access")
    op.drop_index("ix_widgets_widget_id", table_name="widgets")
    op.drop_index("ix_widgets_org_id", table_name="widgets")
    op.drop_table("widgets")
