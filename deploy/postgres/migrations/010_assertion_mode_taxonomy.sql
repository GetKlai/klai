-- Migration 010: Align assertion_mode vocabulary (SPEC-TAXONOMY-001)
-- Run: docker exec -i klai-core-postgres-1 psql -U klai -d klai < 010_assertion_mode_taxonomy.sql
-- Idempotent: safe to run multiple times.

BEGIN;

-- Step 1: Drop the existing CHECK constraint on assertion_mode
ALTER TABLE knowledge.artifacts DROP CONSTRAINT IF EXISTS artifacts_assertion_mode_check;

-- Step 2: Migrate data from old vocabulary to new vocabulary
UPDATE knowledge.artifacts SET assertion_mode = 'fact'        WHERE assertion_mode = 'factual';
UPDATE knowledge.artifacts SET assertion_mode = 'claim'       WHERE assertion_mode = 'belief';
UPDATE knowledge.artifacts SET assertion_mode = 'speculation'  WHERE assertion_mode = 'hypothesis';
UPDATE knowledge.artifacts SET assertion_mode = 'unknown'     WHERE assertion_mode IS NULL;

-- Step 3: Add the new CHECK constraint with the 6 new values
ALTER TABLE knowledge.artifacts ADD CONSTRAINT artifacts_assertion_mode_check
    CHECK (assertion_mode IN ('fact', 'claim', 'speculation', 'procedural', 'quoted', 'unknown'));

COMMIT;
