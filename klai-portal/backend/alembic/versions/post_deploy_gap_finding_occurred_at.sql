-- Re-date existing case-backed gap rows by the evidence they cite.
--
-- Until this deploy a finding took its occurred_at from the insert, so every
-- row in the knowledge inbox carried the time of the last analysis run instead
-- of the moment the customer raised the question. The application now dates a
-- finding by the messages it cites (app/services/support_cases.py::
-- _finding_occurred_at), but existing rows only pick that up if their case is
-- ever reanalysed, which for unchanged evidence never happens.
--
-- Idempotent on purpose: the deploy script applies every post_deploy_*.sql on
-- every rollout, and this statement recomputes the same value from the same
-- evidence, so a second run updates nothing (the WHERE clause makes that
-- explicit rather than relying on equal writes).
--
-- Same order as the code: the newest cited message that carries a timestamp,
-- then the newest message at all, then the source stamp, then the import.
BEGIN;

WITH dated AS (
    SELECT
        g.id,
        COALESCE(
            (
                SELECT max((m ->> 'occurred_at')::timestamptz)
                FROM jsonb_array_elements(c.payload -> 'messages') AS m
                WHERE jsonb_typeof(c.payload -> 'messages') = 'array'
                  AND m ->> 'occurred_at' IS NOT NULL
                  AND m ->> 'id' IN (
                      SELECT jsonb_array_elements_text(COALESCE(g.evidence -> 'message_ids', '[]'::jsonb))
                  )
            ),
            (
                SELECT max((m ->> 'occurred_at')::timestamptz)
                FROM jsonb_array_elements(c.payload -> 'messages') AS m
                WHERE jsonb_typeof(c.payload -> 'messages') = 'array'
                  AND m ->> 'occurred_at' IS NOT NULL
            ),
            (c.payload ->> 'source_updated_at')::timestamptz,
            c.imported_at
        ) AS evidence_at
    FROM public.portal_retrieval_gaps g
    JOIN public.portal_support_cases c ON c.id = g.support_case_id
    WHERE g.support_case_id IS NOT NULL
)
UPDATE public.portal_retrieval_gaps g
SET occurred_at = dated.evidence_at
FROM dated
WHERE g.id = dated.id
  AND dated.evidence_at IS NOT NULL
  AND g.occurred_at IS DISTINCT FROM dated.evidence_at;

COMMIT;
