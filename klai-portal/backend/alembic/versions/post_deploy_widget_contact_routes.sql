-- Give every widget with contact settings a widget_config.contact_routes.
--
-- Expand only. The new code reads contact_routes and nothing else
-- (app/services/contact_routes.py); the old keys stay in place, because this
-- file commits before the new container replaces the old one and a rollback
-- puts the previous image back on this data without undoing it
-- (deploy/scripts/deploy-portal-api.sh). Until the old keys are dropped in a
-- later release, the old code keeps its appointment button and its referral.
--
-- A widget without contact_routes that has at least one old setting gets one
-- default route named "Support": its booking URL (the embedded-booking
-- integration's URL when that toggle is on, else booking_url, else the
-- integration's URL), contact_email and contact_phone, and, only when BOTH
-- off_topic_subjects and off_topic_reply are set (the old referral ran only
-- then), the subjects as do_not_answer and the reply as referral_text. A
-- widget that already has contact_routes is left untouched.
--
-- Values are copied as they are. A value the admin API would refuse is
-- dropped when the route is read (routes_from_config logs which field), so
-- this file never stops a deploy and never loses what a tenant wrote.
-- Idempotent: once a widget has contact_routes, nothing here touches it again.
BEGIN;

SET LOCAL app.cross_org_admin = 'true';

WITH legacy AS (
    SELECT id,
           COALESCE(
               CASE WHEN widget_config #> '{integrations,appointments,enabled}' = 'true'::jsonb
                    THEN NULLIF(btrim(widget_config #>> '{integrations,appointments,booking_url}'), '') END,
               NULLIF(btrim(widget_config ->> 'booking_url'), ''),
               NULLIF(btrim(widget_config #>> '{integrations,appointments,booking_url}'), '')
           ) AS booking_url,
           NULLIF(btrim(widget_config ->> 'contact_email'), '') AS email,
           NULLIF(btrim(widget_config ->> 'contact_phone'), '') AS phone,
           NULLIF(btrim(widget_config ->> 'off_topic_subjects'), '') AS subjects,
           NULLIF(btrim(widget_config ->> 'off_topic_reply'), '') AS reply
      FROM public.widgets
     WHERE NOT widget_config ? 'contact_routes'
)
UPDATE public.widgets w
   SET widget_config = w.widget_config || jsonb_build_object(
           'contact_routes',
           jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
               'id', 'support',
               'name', 'Support',
               'booking_url', l.booking_url,
               'email', l.email,
               'phone', l.phone,
               'do_not_answer', CASE WHEN l.reply IS NOT NULL THEN l.subjects END,
               'referral_text', CASE WHEN l.subjects IS NOT NULL THEN l.reply END,
               'is_default', true
           )))
       )
  FROM legacy l
 WHERE w.id = l.id
   AND (l.booking_url IS NOT NULL OR l.email IS NOT NULL OR l.phone IS NOT NULL
        OR (l.subjects IS NOT NULL AND l.reply IS NOT NULL));

COMMIT;

SELECT 'post_deploy_widget_contact_routes applied' AS status;
