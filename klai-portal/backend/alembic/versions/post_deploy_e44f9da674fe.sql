-- post_deploy_e44f9da674fe.sql
-- SPEC-RAG-MULTILINGUAL-CHAT-001 cleanup: drop the residual NL-pinning
-- string from the seeded "Klantenservice" default template across all
-- tenants.
--
-- Run as klai superuser (NOT portal_api). portal_templates uses the
-- strict Cat-D RLS pattern (org_id = NULLIF(current_setting(...))::int)
-- with no IS NULL branch — portal_api without a tenant GUC sees zero
-- rows. klai bypasses RLS and can rewrite every tenant's row in one
-- statement.
--
-- Idempotent: the LIKE filter only matches rows that still contain the
-- legacy "Antwoord altijd in het Nederlands" substring, so re-running
-- this script after the first successful application is a zero-row
-- no-op.
--
-- Why a post-deploy file and not the alembic upgrade():
--     See the docstring in e44f9da674fe_data_drop_nl_pinning_from_.py.

BEGIN;

WITH updated AS (
    UPDATE portal_templates
    SET prompt_text = 'Je bent een behulpzame klantenservicemedewerker. '
                   || 'Gebruik een vriendelijke en professionele toon, in '
                   || 'dezelfde taal als de vraag van de gebruiker. Houd '
                   || 'antwoorden kort en bondig. Bied proactief oplossingen '
                   || 'aan. Als je het antwoord niet weet, zeg dat eerlijk '
                   || 'en verwijs door naar de juiste afdeling.'
    WHERE slug = 'klantenservice'
      AND prompt_text LIKE '%Antwoord altijd in het Nederlands%'
    RETURNING 1
)
-- Surface the row count so apply_post_deploy_sql.sh's tail-3 output
-- reports something meaningful per run. First successful run reports
-- the number of tenants migrated; idempotent re-runs report 0.
SELECT 'rows_updated' AS marker, COUNT(*) AS n FROM updated;

COMMIT;
