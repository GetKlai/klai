---
name: chat-evolution
description: The weekly reading of real widget conversations for the chat measuring stick. Use when reading last week's conversations for wrong or failed answers, adding a case to the measuring stick (meetlat), or writing a reading into the evolution document (docs/architecture/chat-quality-history-and-plan.md).
---

# Chat evolution: the weekly reading

Design: `docs/architecture/conversation-retention-and-learning.md` §5. Real conversations exist only in the database, for the retention term. What survives a reading is a newly written case in the test set and an entry in the evolution document, never a conversation.

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

Group the first-failure points into patterns. Compare each pattern with the existing test set (one file per organisation, see below). A pattern that already has a case gets no new one. Write one new case per new pattern, not one per conversation. If the lesson can only be told with one visitor's circumstances, write no case.

## 4. Rules for a case (design §5.1)

- Rephrase the question in general words about the product. Take over no sentence or phrase of the visitor.
- No name, e-mail address, phone number, customer number, address, visitor's company, date or amount from the conversation. If an example value is needed, invent it.
- No conversation id, timestamp or other field that points back.
- Format: the widget case meetlat reads, one JSON object per line: `id`, `messages` (a list of `{"role", "content"}`, the last one a user turn; earlier assistant replies are sent along as history, so write those newly too and keep what the next turn depends on), `expect` (or `class`) with one of `antwoord`, `vraag`, `niet_gevonden`, `doorverwijzing`, `afsluiting`, and optionally `articles` (the URLs of the articles that should carry the answer) and `variants`. A case whose expectation the product owner has not confirmed carries `"needs_owner": true`. See `docs/runbooks/meetlat.md`.

Before a case is added, run it:

```bash
python scripts/meetlat.py check-cases --org <org-slug> --cases <cases.jsonl>
```

Exit 0 is required. A failure names the case and the rule (e-mail address, six-digit run, or the number of words shared with a stored visitor message). Rewrite the message until it passes; do not shorten the check.

## 5. Where the test set lives

Outside this repository: one file per organisation in the private repository that holds customer material, in the format meetlat reads, so every addition is a line in its history. Add the new cases there, not here, and copy the file to the server next to the widget description when a round needs it.

## 6. Write the reading into the evolution document

Add one entry to §8a of `docs/architecture/chat-quality-history-and-plan.md` with the paragraphs `Verwachting:`, `Uitkomst:` and `Besluit:`. State what was read (counts per source), which patterns were found, and how many cases were added per pattern. No conversation ids, no quotes, no visitor wording.

## 7. Clean up

Working files with real conversations stay outside the repository and are deleted when the reading is done.
