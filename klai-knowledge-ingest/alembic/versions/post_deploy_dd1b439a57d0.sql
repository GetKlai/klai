-- SPEC-TI-003: Post-deploy RLS policies for knowledge.* schema
-- Run as klai superuser AFTER alembic upgrade head (rev dd1b439a57d0) completes.
-- Idempotent: all statements use OR REPLACE / DROP POLICY IF EXISTS.

-- 1. Helper: raises 42501 when tenant context missing (Cat-D fail-loud).
--    Returns TEXT (knowledge.* org_id is Zitadel resourceowner string).
CREATE OR REPLACE FUNCTION knowledge._rls_current_org_id()
  RETURNS text
  LANGUAGE plpgsql
  STABLE
  SECURITY DEFINER
AS $$
DECLARE
  v_org_id text;
BEGIN
  v_org_id := current_setting('app.current_org_id', true);
  IF v_org_id IS NULL OR v_org_id = '' THEN
    RAISE EXCEPTION
      'RLS: app.current_org_id is not set -- use tenant_scoped_connection()'
      USING ERRCODE = '42501';
  END IF;
  RETURN v_org_id;
END;
$$;

ALTER FUNCTION knowledge._rls_current_org_id() OWNER TO klai;

-- 2. Cat-D policies on tables with org_id column (9 tables).

DROP POLICY IF EXISTS tenant_isolation ON knowledge.artifacts;
CREATE POLICY tenant_isolation ON knowledge.artifacts
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.entities;
CREATE POLICY tenant_isolation ON knowledge.entities
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.crawl_domains;
CREATE POLICY tenant_isolation ON knowledge.crawl_domains
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.crawl_jobs;
CREATE POLICY tenant_isolation ON knowledge.crawl_jobs
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.crawled_pages;
CREATE POLICY tenant_isolation ON knowledge.crawled_pages
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.kb_config;
CREATE POLICY tenant_isolation ON knowledge.kb_config
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.org_config;
CREATE POLICY tenant_isolation ON knowledge.org_config
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.page_links;
CREATE POLICY tenant_isolation ON knowledge.page_links
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

DROP POLICY IF EXISTS tenant_isolation ON knowledge.parent_chunks;
CREATE POLICY tenant_isolation ON knowledge.parent_chunks
  AS RESTRICTIVE
  USING (org_id = knowledge._rls_current_org_id())
  WITH CHECK (org_id = knowledge._rls_current_org_id());

-- 3. Junction-table policies (subquery via knowledge.artifacts).

DROP POLICY IF EXISTS tenant_isolation ON knowledge.artifact_entities;
CREATE POLICY tenant_isolation ON knowledge.artifact_entities
  AS RESTRICTIVE
  USING (
    artifact_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  )
  WITH CHECK (
    artifact_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  );

DROP POLICY IF EXISTS tenant_isolation ON knowledge.artifact_images;
CREATE POLICY tenant_isolation ON knowledge.artifact_images
  AS RESTRICTIVE
  USING (
    artifact_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  )
  WITH CHECK (
    artifact_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  );

-- SPEC-TI-003-FOLLOWUP-001 AC-8: knowledge.derivations has columns (child_id,
-- parent_id) -- there is no source_id column. The original SPEC-TI-003 SQL
-- referenced source_id, which would have failed at apply-time; a hot-fix on
-- 2026-05-06 reapplied the policy with child_id directly on prod. This back-
-- fills that hot-fix into source. child_id is the new artifact in a
-- derivation pair; gating reads/writes on child_id ownership matches how the
-- pipeline produces derivations (always for the local org's child).
DROP POLICY IF EXISTS tenant_isolation ON knowledge.derivations;
CREATE POLICY tenant_isolation ON knowledge.derivations
  AS RESTRICTIVE
  USING (
    child_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  )
  WITH CHECK (
    child_id IN (
      SELECT id FROM knowledge.artifacts
      WHERE org_id = knowledge._rls_current_org_id()
    )
  );

-- 4. embedding_queue: [DRAFT] permissive pending FK schema verification.
--    SPEC-TI-003 AC-4: confirm FK column name, then replace 'true' with subquery.
DROP POLICY IF EXISTS tenant_isolation ON knowledge.embedding_queue;
CREATE POLICY tenant_isolation ON knowledge.embedding_queue
  AS PERMISSIVE
  USING (true)
  WITH CHECK (true);

-- 5. Grant EXECUTE on helper to application roles.
GRANT EXECUTE ON FUNCTION knowledge._rls_current_org_id() TO knowledge_ingest;
GRANT EXECUTE ON FUNCTION knowledge._rls_current_org_id() TO retrieval_api;
-- Add other app roles as new services gain knowledge.* access.
