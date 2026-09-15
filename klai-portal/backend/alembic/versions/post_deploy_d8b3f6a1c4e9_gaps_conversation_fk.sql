-- Post-deploy SQL for revision d8b3f6a1c4e9 (gap provenance columns).
-- SPEC-KNOWLEDGE-ACTIVITY-001 §4.5.
--
-- portal_api lacks the REFERENCES privilege on `widget_conversations` (owned by
-- klai), so ADD CONSTRAINT ... FOREIGN KEY REFERENCES widget_conversations(id)
-- inside alembic's upgrade() fails with 42501 — same split as
-- b7e4f1a9c3d2_conversation_quality_judgments. The two columns and their index
-- are created by the alembic revision; the foreign key is added here, applied
-- by an operator (or scripts/apply_post_deploy_sql.sh) as klai superuser AFTER
-- `alembic upgrade head` succeeds.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_d8b3f6a1c4e9_gaps_conversation_fk.sql"
--
-- Idempotent: the ADD CONSTRAINT is guarded by a pg_constraint lookup, so
-- re-runs are safe. ON DELETE SET NULL, not CASCADE: the gap is the record that
-- the KB could not answer, and it must outlive the conversation it came from —
-- widget_messages_retention purges conversations precisely when the gap becomes
-- most interesting. Every existing row has conversation_id NULL (the column is
-- new), so the constraint validates without a backfill.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'portal_retrieval_gaps'::regclass
          AND conname = 'fk_retrieval_gaps_conversation'
    ) THEN
        ALTER TABLE portal_retrieval_gaps
            ADD CONSTRAINT fk_retrieval_gaps_conversation
            FOREIGN KEY (conversation_id)
            REFERENCES widget_conversations (id)
            ON DELETE SET NULL;
    END IF;
END
$$;

COMMIT;
