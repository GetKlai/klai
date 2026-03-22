-- Migration: 001_knowledge_schema.sql
-- Run manually (PostgreSQL is already running — init.sql only runs on first startup):
--   docker exec -i klai-core-postgres-1 psql -U klai -d klai < 001_knowledge_schema.sql
-- Idempotent: safe to run multiple times.

CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE TABLE IF NOT EXISTS knowledge.artifacts (
  id                UUID PRIMARY KEY,
  org_id            UUID NOT NULL,
  user_id           UUID,
  provenance_type   TEXT NOT NULL CHECK (provenance_type IN ('observed','extracted','synthesized','revised')),
  assertion_mode    TEXT NOT NULL CHECK (assertion_mode IN ('factual','procedural','quoted','belief','hypothesis')),
  synthesis_depth   SMALLINT NOT NULL DEFAULT 0 CHECK (synthesis_depth BETWEEN 0 AND 4),
  confidence        TEXT CHECK (confidence IN ('high','medium','low')),
  belief_time_start BIGINT NOT NULL,
  belief_time_end   BIGINT NOT NULL DEFAULT 253402300800,
  superseded_by     UUID REFERENCES knowledge.artifacts(id),
  created_at        BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge.derivations (
  child_id   UUID NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
  parent_id  UUID NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
  span_json  JSONB,
  PRIMARY KEY (child_id, parent_id)
);

CREATE TABLE IF NOT EXISTS knowledge.entities (
  id         UUID PRIMARY KEY,
  org_id     UUID NOT NULL,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL CHECK (type IN ('product_area','feature','concept','person')),
  created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge.artifact_entities (
  artifact_id UUID NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
  entity_id   UUID NOT NULL REFERENCES knowledge.entities(id) ON DELETE CASCADE,
  resolved    BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (artifact_id, entity_id)
);

CREATE TABLE IF NOT EXISTS knowledge.embedding_queue (
  id           UUID PRIMARY KEY,
  artifact_id  UUID NOT NULL,
  operation    TEXT NOT NULL CHECK (operation IN ('upsert','delete')),
  created_at   BIGINT NOT NULL,
  processed_at BIGINT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_artifacts_org_id ON knowledge.artifacts(org_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_user_id ON knowledge.artifacts(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_artifacts_active ON knowledge.artifacts(belief_time_end);
CREATE INDEX IF NOT EXISTS idx_embedding_queue_unprocessed ON knowledge.embedding_queue(created_at) WHERE processed_at IS NULL;

-- Grant access to the klai user (already owns the klai database)
GRANT USAGE ON SCHEMA knowledge TO klai;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA knowledge TO klai;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA knowledge TO klai;
ALTER DEFAULT PRIVILEGES IN SCHEMA knowledge GRANT ALL ON TABLES TO klai;
