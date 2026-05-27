"""add partner API key rotation metadata

Revision ID: c9d8e7f6a5b4
Revises: fb1c2d3e4a5b
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "fb1c2d3e4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = """
DO $$
BEGIN
    ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotated_from_key_id UUID;
    ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotated_to_key_id UUID;
    ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotation_started_at timestamptz;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_partner_api_keys_rotated_from'
    ) THEN
        ALTER TABLE partner_api_keys
            ADD CONSTRAINT fk_partner_api_keys_rotated_from
            FOREIGN KEY (rotated_from_key_id) REFERENCES partner_api_keys(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_partner_api_keys_rotated_to'
    ) THEN
        ALTER TABLE partner_api_keys
            ADD CONSTRAINT fk_partner_api_keys_rotated_to
            FOREIGN KEY (rotated_to_key_id) REFERENCES partner_api_keys(id)
            ON DELETE SET NULL;
    END IF;

    CREATE INDEX IF NOT EXISTS ix_partner_api_keys_rotated_from_key_id
        ON partner_api_keys (rotated_from_key_id);
    CREATE INDEX IF NOT EXISTS ix_partner_api_keys_rotated_to_key_id
        ON partner_api_keys (rotated_to_key_id);
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping partner_api_keys rotation metadata DDL: migration role is not the owner. post_deploy_c9d8e7f6a5b4_add_partner_api_key_rotation.sql must apply it as klai superuser.';
END
$$;
"""


DOWNGRADE_SQL = """
DO $$
BEGIN
    DROP INDEX IF EXISTS ix_partner_api_keys_rotated_to_key_id;
    DROP INDEX IF EXISTS ix_partner_api_keys_rotated_from_key_id;
    ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS fk_partner_api_keys_rotated_to;
    ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS fk_partner_api_keys_rotated_from;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS rotation_started_at;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS rotated_to_key_id;
    ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS rotated_from_key_id;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping partner_api_keys rotation metadata rollback: migration role is not the owner.';
END
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
