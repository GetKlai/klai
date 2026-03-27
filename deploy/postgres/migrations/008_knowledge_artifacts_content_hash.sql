-- Migration: 008_knowledge_artifacts_content_hash.sql
-- Adds content_hash column to knowledge.artifacts for ingest deduplication.
-- When content_hash matches the stored value for the active artifact, the
-- ingest pipeline skips chunking, embedding, enrichment, and Qdrant upserts.
-- Safe: additive column with nullable default, no data loss.

ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- Composite index used by the content-hash lookup in pg_store.get_active_content_hash:
--   WHERE org_id=$1 AND kb_slug=$2 AND path=$3 AND belief_time_end=$4
CREATE INDEX IF NOT EXISTS idx_artifacts_active_path
    ON knowledge.artifacts(org_id, kb_slug, path, belief_time_end);
