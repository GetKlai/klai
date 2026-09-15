-- Post-deploy SQL for revision 997e0b66f750 (gap resolution provenance).
-- SPEC-KNOWLEDGE-ACTIVITY-001 §4.5/§4.9.
--
-- portal_api lacks the REFERENCES privilege on `portal_users` (owned by
-- klai), so ADD CONSTRAINT ... FOREIGN KEY REFERENCES portal_users(id)
-- inside alembic's upgrade() fails with 42501 -- same split as
-- d8b3f6a1c4e9_gaps_conversation_language / post_deploy_d8b3f6a1c4e9. The two
-- columns and their CHECK constraint are created by the alembic revision;
-- the foreign key is added here, applied by an operator (or
-- scripts/apply_post_deploy_sql.sh) as klai superuser AFTER
-- `alembic upgrade head` succeeds.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_997e0b66f750_gaps_resolved_by_fk.sql"
--
-- Idempotent: the ADD CONSTRAINT is guarded by a pg_constraint lookup, so
-- re-runs are safe. ON DELETE SET NULL, not CASCADE: a departed colleague
-- must not take the gap-closure history with them -- the gap stays resolved,
-- only the attribution to that user is cleared. Every existing row has
-- resolved_by_user_id NULL (the column is new), so the constraint validates
-- without a backfill.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'portal_retrieval_gaps'::regclass
          AND conname = 'fk_retrieval_gaps_resolved_by_user'
    ) THEN
        ALTER TABLE portal_retrieval_gaps
            ADD CONSTRAINT fk_retrieval_gaps_resolved_by_user
            FOREIGN KEY (resolved_by_user_id)
            REFERENCES portal_users (id)
            ON DELETE SET NULL;
    END IF;
END
$$;

COMMIT;
