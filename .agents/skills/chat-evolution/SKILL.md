---
name: chat-evolution
description: The weekly reading of real widget conversations for the chat measuring stick. Use when reading last week's conversations for wrong or failed answers, adding a case to the measuring stick (meetlat), or writing a reading into the evolution document (docs/architecture/chat-quality-history-and-plan.md).
---

# Chat evolution: the weekly reading

Design: `docs/architecture/conversation-retention-and-learning.md` §5. The test set of the measuring stick consists of real conversations chosen as test cases, a deliberate exception to the retention term (decided 15 November 2026). What survives a reading is a candidate case in the test set, which lives outside this repository, and an entry in the evolution document without conversation ids or quotes.

## 1. Pick the conversations of the past week

Run the queries read-only through the database route of the private infrastructure runbook; this repository names no host. Wrap them in `BEGIN READ ONLY; ... ROLLBACK;`. A read-only transaction cannot create a temp table or a view, so combine the sources with subqueries or CTEs. Per organisation (`:org_id`), `since = now() - interval '7 days'`. Preview and test conversations (`widget_conversations.is_preview`, `is_test`) and the E2E organisation (`portal_orgs.slug = 'e2e'`) never count. Re-check the column names against `app/models/answer_reviews.py`, `conversation_quality.py` and `widgets.py` before you rely on them.

- Turns a colleague judged wrong: `answer_reviews` where `verdict IN ('wrong','incomplete')` and `reviewed_at >= since`. Join `widget_messages` on `message_id` for the turn; `message_id` is NULL once the conversation is purged, so skip those.
- Conversations the nightly judge marked as failed (keep only those that still have rows in `widget_messages`: a verdict outlives the conversation's text, and a conversation whose messages are gone cannot be read): `conversation_quality_judgments` where `channel = 'webchat'`, `judged_at >= since` and (`outcome IN ('unresolved','partially_resolved')` or `failure_category IN ('retrieval_miss','retrieval_wrong','generation_error')`). `conversation_id` joins `widget_conversations.id`.
- Turns the knowledge-gap list counts: assistant rows of `widget_messages` where `answer_signals->>'decision' IN ('refusal','clarifying_question','human_request','partial_answer')` or `answer_signals->>'gap_type' = 'soft'` (the rule is `is_gap` / `GAP_DECISIONS` in `app/services/knowledge_gap_report.py`). `off_topic` is a decision of its own and is not a gap.
- A random sample of the rest: assistant rows in none of the three sets, `ORDER BY random() LIMIT 5`, so what looks fine is read too.

```sql
SELECT m.conversation_id, m.sequence
FROM widget_messages m
JOIN widget_conversations c ON c.id = m.conversation_id
WHERE m.org_id = :org_id AND m.role = 'assistant' AND m.created_at >= :since
  AND NOT c.is_preview AND NOT c.is_test
  AND (m.answer_signals->>'decision' IN ('refusal','clarifying_question','human_request','partial_answer')
       OR m.answer_signals->>'gap_type' = 'soft');
```

Aim for ten to twenty conversations in total. Read each in full, from the first visitor message. Keep the output in a working directory outside the repository.

## 2. Follow each case to the end

For each conversation, trace the path the answer took (question, search, selection, answer, check) and record the first point where it goes wrong. The working agreements in the evolution document (§6) require this: a count per component hides the cause.

## 3. Group into patterns and compare with the test set

Group the first-failure points into patterns. Compare each pattern with the existing test set (one file per organisation, outside this repository). One case per pattern: a pattern that already has a case gets none.

## 4. Make a candidate case from the real conversation

A new failure pattern becomes a candidate case made from the real conversation as it was: the visitor's turns and the replies up to the turn to replay, not reworded.

- The visitor's name and e-mail address never go into a case.
- Format: the widget case meetlat reads, one JSON object per line: `id`, `messages` (a list of `{"role", "content"}`, the last one a user turn), `expect` with one of `antwoord`, `vraag`, `niet_gevonden`, `doorverwijzing`, `afsluiting`, and optionally `articles` (the URLs of the articles that should carry the answer). See `docs/runbooks/meetlat.md`.
- The case carries `"needs_owner": true` until the product owner confirms the expectation.

## 5. Where the test set lives

Outside this repository, next to the widget description; copy the file to the server when a round needs it. Add the candidate cases there, not here.

## 6. Write the reading into the evolution document

Add one entry to §8a of `docs/architecture/chat-quality-history-and-plan.md` with the paragraphs `Verwachting:`, `Uitkomst:` and `Besluit:`. State what was read (counts per source), which patterns were found, and how many cases were added per pattern. No conversation ids, no quotes, no visitor wording.

## 7. Clean up

Working copies of conversations made on a development machine are deleted when the reading is done.
