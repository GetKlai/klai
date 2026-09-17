# REQ-0 baseline eval - SPEC-RAG-CLARIFY-FLOW-001 (path B)

Measures how the public widget handles vague, answerable, and
not-in-knowledge-base questions BEFORE REQ-2/REQ-3 change its behaviour.
Lives here (not `klai-retrieval-api/evaluation/` or
`klai-knowledge-ingest/knowledge_ingest/eval/`) because it drives
portal-api's own public endpoints end to end, the same two calls a
visitor's browser makes - no other service's eval harness reaches those
routes. Follows the sibling-`evaluation/` + `tests/test_<runner>.py`
layout `klai-retrieval-api` already uses.

## Run it

```bash
cd klai-portal/backend
uv run python evaluation/clarify_flow_runner.py
```

Writes `evaluation/results/clarify_flow_baseline.jsonl` (one row per
turn) and `evaluation/results/clarify_flow_baseline_summary.md` (counts
per category x outcome, plus every `answer_without_sources` /
`clarifying_question` turn listed for manual review). Prints the summary
to stdout. Exit code 1 only if every single request failed; a mix of
failures and successes still exits 0 so the summary always gets written.

Flags: `--questions PATH`, `--output-dir PATH`, `--samples N` (repeats
per question, default 1), `--delay SECONDS` (pause between requests,
default 2.0), `--base-url`, `--widget-id`, `--origin` (defaults target
Klai's own "Klai Website NL" widget on getklai.com).

## Tests

```bash
uv run pytest tests/test_clarify_flow_runner.py
```

Covers the two things with actual branches: the SSE parser
(`parse_sse_stream`) and the outcome classifier (`classify_turn`), both
pure functions, fixed input, no network.

## Test traffic

Every run creates real `widget_conversations` rows on Klai's own tenant.
Two existing mechanisms exclude a conversation from knowledge activity,
gap detection, and the nightly judge - neither is reachable from the
public widget flow this runner is scoped to (spec.md REQ-0: "via
`/partner/v1/widget-config` en `/partner/v1/chat/completions` met een
widgetsessie"):

- **`widget_conversations.is_test`** - set by `PUT
  /conversations/{conversation_id}/test`
  (`klai-portal/backend/app/api/app_activity.py:981`). Requires
  `get_caller` (an authenticated portal-user session with `perms.org_id`)
  and a `conversation_id` the public chat API never returns. It is a
  reviewer marking a conversation as a test AFTER the fact through the
  portal UI, not something a laptop script can call.
- **`widget_conversations.is_preview`** - set by minting the session
  token through `GET /admin/widgets/{widget_id}/preview-session`
  (`klai-portal/backend/app/api/admin_widgets.py:540-587`), which requires
  `get_caller_at_least(ProfileRole.ADMIN)` (portal admin cookie). The
  public `GET /partner/v1/widget-config` this runner calls never sets
  `is_preview` - confirmed live: the widget-config response's JWT decodes
  to `"is_preview": false`.

No production code was changed to add a marker: neither path is reachable
without portal-admin authentication this runner intentionally does not
have. Consequence: every baseline run (and every live spot-check below)
shows up as ordinary visitor traffic in Klai's own activity dashboard and
knowledge-gap backlog until a human marks it via the portal UI.

## Live-checked questions

Before finalizing `clarify_flow_questions.yaml`, six borderline questions
were asked directly against the widget to confirm the category:

| question | category | result |
|---|---|---|
| "Bieden jullie een VPN-koppeling aan?" | not_in_kb | canned refusal (base NL) |
| "Wat kost Klai per gebruiker per maand?" | answerable | answered, 1 source |
| "integraties" | vague | canned refusal |
| "prijzen?" | vague | canned refusal |
| "Is er een SSO integratie met Azure AD of Okta?" | not_in_kb | canned refusal |
| "Bieden jullie een uptime SLA van 99.9% met financiele garantie?" | not_in_kb-adjacent | answered with 1 source, honest "not covered" - reclassified as answerable-shaped negative, not used verbatim in the set |

These also confirmed: this widget's `support_mode` is `false`, so its
canned refusal is the BASE variant (`"Ik kan dit niet betrouwbaar
beantwoorden op basis van de beschikbare kennisbronnen."`), not the
customer-facing helpdesk variant - `classify_turn` still checks both
(and both languages) since `helpdesk` is a per-widget setting the spec's
other target widgets may have on.
