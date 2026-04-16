-- Migration 002: idempotency_keys table for page creation dedup
-- Apply manually: psql -h <host> -U klai -d klai -f migrations/002_idempotency_keys.sql

-- Stores idempotency keys for page creation requests.
-- TTL is enforced at application level (keys older than 24h are ignored).
-- Unique constraint on (kb_id, key) prevents duplicate page creation.
CREATE TABLE IF NOT EXISTS docs.idempotency_keys (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id       uuid        NOT NULL REFERENCES docs.knowledge_bases(id) ON DELETE CASCADE,
    key         text        NOT NULL,   -- client-supplied Idempotency-Key header value
    page_slug   text        NOT NULL,   -- slug of the page that was created
    created_at  timestamptz DEFAULT now(),
    UNIQUE (kb_id, key)
);

CREATE INDEX IF NOT EXISTS idempotency_keys_kb_id_idx ON docs.idempotency_keys (kb_id);
CREATE INDEX IF NOT EXISTS idempotency_keys_created_at_idx ON docs.idempotency_keys (created_at);
