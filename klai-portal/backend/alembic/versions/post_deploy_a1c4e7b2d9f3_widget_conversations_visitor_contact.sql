-- widget_conversations.visitor_name / visitor_email
--
-- The widget's pre-chat step (widget_config.collect_user_info) asks the
-- visitor for a name and an e-mail so a reviewer can mail a correction when
-- the AI answer was wrong. Both columns are nullable: the step can be skipped,
-- and every conversation that predates it has no contact details at all.
--
-- Lengths match the request-model limits in partner.py
-- (ChatCompletionsRequest.visitor_name / visitor_email), which in turn match
-- the HubSpot handoff request that already carried the same two fields.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision a1c4e7b2d9f3.
--
-- No RLS change: the existing Cat-D tenant_isolation policy on
-- widget_conversations covers new columns on the same table.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_conversations'
          AND column_name = 'visitor_name'
    ) THEN
        ALTER TABLE widget_conversations ADD COLUMN visitor_name VARCHAR(120);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_conversations'
          AND column_name = 'visitor_email'
    ) THEN
        ALTER TABLE widget_conversations ADD COLUMN visitor_email VARCHAR(254);
    END IF;
END $$;
