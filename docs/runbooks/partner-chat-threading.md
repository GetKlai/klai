# Partner API — Conversation Threading & Long Context

> Integration guidance for partners using `POST /partner/v1/chat/completions`.
> Platform internals live in `docs/architecture/conversation-state.md`.

## The contract: you own the thread

The Klai partner chat API is **stateless**, like OpenAI Chat Completions,
Anthropic Messages, and Mistral chat completions: you send the full
`messages` array on every request, and Klai keeps nothing between turns.
This is the industry-default contract — server-side conversation stores at
the big vendors are a convenience layer and still bill all replayed history
as input tokens on every turn.

Practically:

```json
{
  "model": "klai-primary",
  "messages": [
    {"role": "system", "content": "<your instructions — keep this stable>"},
    {"role": "user", "content": "turn 1"},
    {"role": "assistant", "content": "answer 1"},
    {"role": "user", "content": "turn 2"}
  ]
}
```

## Keep the window bounded

Do not let the array grow forever. Klai's own surfaces use windows too:
the Klai widget keeps the last 80 messages; the Klai HubSpot integration
sends the last 6 turns at max 1 200 chars each. A sliding window of the
last 6–12 turns is enough for almost all support flows — the knowledge
path only uses the last 3 prior turns for query rewriting anyway.

Hard limits to design against:

| Limit | Value |
|---|---|
| Request body | 128 KB |
| Estimated input tokens (general passthrough) | 16 000 |
| `max_tokens` output cap (general passthrough) | 4 096 (default 2 048) |
| History entry sent to knowledge retrieval | clipped server-side at 7 800 chars |

## Cut costs with `prompt_cache_key` (general passthrough)

On the general passthrough path (requests without knowledge fields, key
with `general_chat`), send a stable `prompt_cache_key` per conversation:

```json
{
  "model": "klai-primary",
  "prompt_cache_key": "ticket-84213",
  "messages": [ ... ]
}
```

Mistral then serves the unchanged prefix of the conversation from cache and
bills those tokens at **10% of the normal input price**. Rules of thumb:

- Use one key per conversation/ticket (max 256 chars).
- Keep the prefix stable: system prompt first, history appended at the end,
  never mutate earlier turns. Any change to an earlier byte invalidates the
  cache from that point on.
- The cache is short-lived (minutes) — it helps active conversations, not
  archives.

## Transcripts and other large artifacts

Three patterns, in order of preference:

1. **Ingest into a knowledge base** (`POST /partner/v1/knowledge`, key with
   `knowledge_append`) and use the knowledge path. Klai retrieves only the
   relevant chunks per question and returns `delta.sources` — you get
   traceability instead of a context-window problem. Best for "assistant
   that can reference past calls/tickets".
2. **Summarize once, inject the summary.** For one-shot tasks over a single
   transcript (categorize, summarize, extract), send the full transcript in
   one turn — that is what the model is for. For multi-turn chat *about* a
   transcript, summarize it once (a `klai-fast` call) and put the summary in
   the system prompt instead of dragging the raw transcript through every
   turn.
3. **Full transcript + `prompt_cache_key`.** If you need the raw transcript
   in-context across turns, put it early in the conversation, keep it
   byte-stable, and use a cache key — repeat turns then bill the transcript
   at 10%.

Mind the caps: one request must stay under 128 KB / ~16 000 input tokens on
the passthrough. A one-hour call transcript usually does not fit — pattern
1 or 2 is the answer, not a bigger window.

## Routing reminder

The API picks its mode by which fields are **present**, not their values.
For passthrough requests (structured output, tool calling, prompt-grounded
tasks): omit `web_search`, `web_search_query`, `page_context` and
`knowledge_base_ids` entirely; `knowledge: {"enabled": false}` is allowed.
Any of those fields present — even `web_search: false` — routes the request
to the knowledge path, where `response_format`, `tools`, `tool_choice`,
`parallel_tool_calls` and `prompt_cache_key` are rejected with a 400
(fail-loud) instead of being silently ignored.
