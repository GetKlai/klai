-- Move every widget's contact settings into widget_config.contact_routes.
--
-- The chat path and the widget-config endpoint read contact_routes only
-- (app/services/contact_routes.py). A widget that has no contact_routes yet and
-- has at least one of the old settings gets one default route named "Support":
-- its booking URL (the embedded-booking integration's URL when that toggle is
-- on, else booking_url, else the integration's URL), contact_email,
-- contact_phone, and, when off_topic_subjects is set, those subjects as the
-- route's do_not_answer with off_topic_reply as its note. A widget that already
-- has contact_routes keeps them untouched. Every widget then loses the old keys.
--
-- Runs as the klai owner: public.widgets is owned by klai with FORCE RLS, and
-- Alembic runs as portal_api (see post_deploy_5b7c9d1e2f3a). deploy-portal-api.sh
-- applies every post_deploy file after `alembic upgrade head` and before the new
-- container replaces the old one, so the new code never reads an unconverted
-- row. Idempotent: once no widget carries an old key, nothing matches.
--
-- A value the admin API would now refuse (a booking URL that is not absolute
-- http(s) or holds brackets, quotes or spaces; a note over 200 characters or
-- holding a link, an address or Markdown characters) aborts the whole file
-- instead of being dropped, so the deploy stops before the swap and nothing a
-- tenant wrote disappears. Fix that row by hand, then deploy again.
BEGIN;

SET LOCAL app.cross_org_admin = 'true';

CREATE TEMP TABLE widget_contact_legacy ON COMMIT DROP AS
SELECT id,
       COALESCE(
           CASE WHEN widget_config #> '{integrations,appointments,enabled}' = 'true'::jsonb
                THEN NULLIF(btrim(widget_config #>> '{integrations,appointments,booking_url}'), '') END,
           NULLIF(btrim(widget_config ->> 'booking_url'), ''),
           NULLIF(btrim(widget_config #>> '{integrations,appointments,booking_url}'), '')
       ) AS booking_url,
       NULLIF(btrim(widget_config ->> 'contact_email'), '') AS email,
       NULLIF(btrim(widget_config ->> 'contact_phone'), '') AS phone,
       NULLIF(btrim(widget_config ->> 'off_topic_subjects'), '') AS do_not_answer,
       CASE WHEN NULLIF(btrim(widget_config ->> 'off_topic_subjects'), '') IS NOT NULL
            THEN NULLIF(btrim(widget_config ->> 'off_topic_reply'), '') END AS note
  FROM public.widgets
 WHERE NOT widget_config ? 'contact_routes';

DELETE FROM widget_contact_legacy
 WHERE booking_url IS NULL AND email IS NULL AND phone IS NULL AND do_not_answer IS NULL;

DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(id::text, ', ') INTO bad
      FROM widget_contact_legacy
     WHERE (booking_url IS NOT NULL AND booking_url !~* '^https?://[^\s()<>\[\]"''`\\]+$')
        OR length(note) > 200
        OR note ~* '[][<>*_`\\@[:cntrl:]]|://|www\.';
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'contact routes: widgets % hold a booking URL or note the admin API refuses; fix them first', bad;
    END IF;
END $$;

UPDATE public.widgets w
   SET widget_config = w.widget_config || jsonb_build_object(
           'contact_routes',
           jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
               'id', 'support',
               'name', 'Support',
               'booking_url', l.booking_url,
               'email', l.email,
               'phone', l.phone,
               'do_not_answer', l.do_not_answer,
               'note', l.note,
               'is_default', true
           )))
       )
  FROM widget_contact_legacy l
 WHERE w.id = l.id;

UPDATE public.widgets
   SET widget_config = (widget_config
           - 'booking_url' - 'contact_email' - 'contact_phone' - 'off_topic_subjects' - 'off_topic_reply')
           #- '{integrations,appointments,booking_url}'
 WHERE widget_config ?| ARRAY['booking_url', 'contact_email', 'contact_phone', 'off_topic_subjects', 'off_topic_reply']
    OR widget_config #> '{integrations,appointments}' ? 'booking_url';

COMMIT;

SELECT 'post_deploy_widget_contact_routes applied' AS status;
