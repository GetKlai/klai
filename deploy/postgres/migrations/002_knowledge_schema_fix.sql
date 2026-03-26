-- Migration: 002_knowledge_schema_fix.sql
-- Changes org_id and user_id from UUID to TEXT to match Zitadel ID format.
-- Zitadel org IDs are 18-digit integer strings (e.g. "362757920133283846"), not UUIDs.
-- Safe: knowledge.artifacts is empty at time of this migration.
-- Run: docker exec -i klai-core-postgres-1 psql -U klai -d klai < 002_knowledge_schema_fix.sql

ALTER TABLE knowledge.artifacts ALTER COLUMN org_id TYPE TEXT;
ALTER TABLE knowledge.artifacts ALTER COLUMN user_id TYPE TEXT;
ALTER TABLE knowledge.entities  ALTER COLUMN org_id TYPE TEXT;
