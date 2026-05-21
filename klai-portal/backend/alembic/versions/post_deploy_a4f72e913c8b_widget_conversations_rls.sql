-- Post-deploy SQL for revision a4f72e913c8b (widget_conversations + widget_messages).
--
-- portal_api is NOT the owner of these tables (created by alembic as
-- klai). Per `.claude/rules/klai/pitfalls/process-rules.md`
-- ENABLE/FORCE RLS + CREATE POLICY must run as klai superuser
-- AFTER `alembic upgrade head` succeeds.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_a4f72e913c8b_widget_conversations_rls.sql"
--
-- Cat-D strict policy (per portal-security.md): every access path
-- must SET app.current_org_id; missing GUC = raise (helper returns
-- NULL -> first OR clause skipped, fallback strict check fires).

BEGIN;

-- widget_conversations -------------------------------------------------
ALTER TABLE widget_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_conversations FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_conversations;
CREATE POLICY tenant_isolation ON widget_conversations
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

-- widget_messages ------------------------------------------------------
ALTER TABLE widget_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_messages FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_messages;
CREATE POLICY tenant_isolation ON widget_messages
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
