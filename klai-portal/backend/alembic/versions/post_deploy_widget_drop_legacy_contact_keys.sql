-- Remove the contact settings contact_routes replaced from every widget that has routes.
--
-- The contract step after post_deploy_widget_contact_routes.sql. The release
-- this file runs under, and the one a rollback puts back, both read only
-- contact_routes; that previous release copies an old key on save only when
-- the row still holds it, so neither misses what this removes.
--
-- Only rows WITH contact_routes are touched. Every deploy applies every
-- post-deploy file in name order, so the conversion file (…_contact_routes.sql)
-- runs first and gives a route to any row with an old setting its rule can
-- carry over, including a row an older image wrote after the previous deploy.
-- A row still without routes after that holds only values the rule declined
-- (blank values, subjects without a reply or a reply without subjects); it
-- keeps them, so nothing a tenant wrote disappears without a route to show it.
-- The two files touch disjoint rows: the conversion file only rows without
-- contact_routes, this one only rows with them, so a re-run of both changes
-- nothing.
--
-- Idempotent: a row without old keys is not matched again.
BEGIN;

SET LOCAL app.cross_org_admin = 'true';

UPDATE public.widgets
   SET widget_config = (widget_config
                        - ARRAY['booking_url', 'contact_email', 'contact_phone', 'off_topic_subjects', 'off_topic_reply'])
                       #- '{integrations,appointments,booking_url}'
 WHERE widget_config ? 'contact_routes'
   AND (widget_config ?| ARRAY['booking_url', 'contact_email', 'contact_phone', 'off_topic_subjects', 'off_topic_reply']
        OR widget_config #> '{integrations,appointments}' ? 'booking_url');

COMMIT;

SELECT 'post_deploy_widget_drop_legacy_contact_keys applied' AS status;
