-- Give existing chat/telemetry gap rows the grouping key every producer now writes.
--
-- Before this deploy only case-backed findings carried a question_key; chat,
-- widget, MCP and review rows had none and the inbox grouped them on their
-- literal query text. With the new grouping the reader falls back to that
-- literal text for a keyless row, so the very same question would show up
-- twice: once as an old keyless group and once under the new key.
--
-- The expression mirrors app/services/support_cases.py::_question_key exactly:
-- the question normalized (whitespace collapsed, trimmed, case-folded), then
-- language, KB slug and audience, joined by '|', with an empty slot where a
-- value is absent. Keep the two in step; a test pins that pairing. One known
-- edge: SQL lower() is not Python's casefold(), so a question containing e.g.
-- 'ß' keys differently here than in the application. That splits such a group
-- until its next row arrives, and nothing worse.
--
-- Idempotent: it only touches rows that still have no key, so a second run
-- (the deploy script applies every post_deploy file on every rollout) finds
-- nothing left to do. A row whose key was later folded onto another group's
-- key by the grouping judge is never revisited here.
--
-- Deliberately NOT touched: a support finding whose stored key still carries
-- the old diagnosis segment. Rewriting those would also rewrite keys the judge
-- folded, which is how a merged group holds together; they pick up the new
-- shape the next time their case is analysed.
BEGIN;

UPDATE public.portal_retrieval_gaps
   SET question_key = concat_ws(
           '|',
           lower(btrim(regexp_replace(query_text, '\s+', ' ', 'g'))),
           coalesce(language, ''),
           coalesce(nearest_kb_slug, ''),
           coalesce(audience, '')
       )
 WHERE question_key IS NULL;

COMMIT;
