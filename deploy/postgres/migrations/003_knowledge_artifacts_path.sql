-- Migration: 003_knowledge_artifacts_path.sql
-- Adds kb_slug and path columns to knowledge.artifacts.
-- Required for soft-delete: invalidating the correct artifact when a document is removed.
-- Safe: knowledge.artifacts is empty at time of this migration.
-- Run: docker exec -i klai-core-postgres-1 psql -U klai -d klai < 003_knowledge_artifacts_path.sql

ALTER TABLE knowledge.artifacts ADD COLUMN IF NOT EXISTS kb_slug TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge.artifacts ADD COLUMN IF NOT EXISTS path   TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_artifacts_org_kb_path
    ON knowledge.artifacts(org_id, kb_slug, path);
