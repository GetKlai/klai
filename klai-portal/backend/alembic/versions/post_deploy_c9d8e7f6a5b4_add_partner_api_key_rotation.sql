-- Post-deploy DDL for c9d8e7f6a5b4.
--
-- partner_api_keys is owned by the klai superuser in production, while the
-- normal Alembic migration runs as the application/migration role. The Python
-- migration attempts this DDL for local/dev databases, but production applies
-- it here as the table owner through deploy-portal-api.sh.
--
-- Idempotent: safe to re-run.

ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotated_from_key_id UUID;
ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotated_to_key_id UUID;
ALTER TABLE partner_api_keys ADD COLUMN IF NOT EXISTS rotation_started_at timestamptz;

DO $$
BEGIN
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
END
$$;

CREATE INDEX IF NOT EXISTS ix_partner_api_keys_rotated_from_key_id
    ON partner_api_keys (rotated_from_key_id);
CREATE INDEX IF NOT EXISTS ix_partner_api_keys_rotated_to_key_id
    ON partner_api_keys (rotated_to_key_id);
