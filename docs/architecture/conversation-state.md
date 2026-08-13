# Conversation State, Threading & Context-Window Management

> Status: **current state** as of 2026-08-12 (code-verified). This doc answers
> "who owns conversation history in Klai, and what happens when it grows?"
> for every chat surface. Design/roadmap decisions live in SPECs, not here.

## TL;DR

**Every Klai chat surface is stateless at the API boundary: the client owns
threading and resends the full `messages` array each turn.** This matches the
industry default (OpenAI Chat Completions, Anthropic Messages, Mistral chat
completions are all stateless; server-side state offerings such as OpenAI
Conversations bill all replayed history as input tokens anyway — state is a
convenience, not a cost saver). The only automatic context-window management
in Klai lives in the LiteLLM layer (`klai_context.py`), which silently drops
oldest turns when a per-model history budget is exceeded. Nothing in Klai
summarizes or compacts chat history today.

## Who owns history, per surface

| Surface | History owner | Sent per request | Server-side store? |
|---|---|---|---|
| LibreChat (path A) | LibreChat MongoDB (per tenant) | Full conversation history | Yes, but UI-state only — `klai_context.py:3` "LibreChat owns UI conversation state. Klai owns the provider-boundary contract." |
| Partner API `/partner/v1/chat/completions` (path B) | The partner | Full `messages` array | No. No conversation/thread id exists on the request. |
| Klai widget | Browser `localStorage`, capped at last 80 messages (`klai-widget/src/store/chat.ts`) | Full array | `widget_conversations` — audit/admin-review grouping only, never read back into a prompt |
| HubSpot email-support card | The card (client-side), last 6 turns @ 1 200 chars each (`klai-hubspot/.../NewFunction.js`) | Client-built payload | `partner_support_sessions` + `partner_support_messages` — per-ticket audit/UI-restore store; **never fed to the model** |
| retrieval-api `/chat` + `/retrieve` | Caller passes `conversation_history` | Max 20 entries, each ≤ 8 000 chars (hard 422 above) | No |

## Partner API: stateless by design

- `POST /partner/v1/chat/completions` takes the full `messages` array every
  turn. There is no `conversation_id`, no `previous_response_id`, no `store`.
- `POST /partner/v1/responses` **hard-rejects** (HTTP 400) every stateful
  OpenAI Responses field: `previous_response_id`, `store`, `conversation`,
  `truncation`, `context_management`, `prompt_cache_key`, … — see
  `_OPENAI_RESPONSES_UNSUPPORTED_FIELDS` in `app/api/partner.py`. Deliberate:
  a stateless adapter that refuses to pretend.
- `partner_support_sessions` (HubSpot integration) is a *storage* API around
  the chat API, not a threading API: the card still builds its own model
  payload client-side.

### Hard limits (partner path)

| Limit | Value | Applies to |
|---|---|---|
| Request body | 128 KB (`_OPENAI_COMPAT_MAX_BODY_BYTES`) | both paths (general passthrough + knowledge) |
| Estimated input tokens (len/4 heuristic) | 16 000 (`partner_openai_max_input_tokens`) | general passthrough + `/responses` only |
| `max_tokens` cap / default | 4 096 / 2 048 | general passthrough + `/responses` |
| General-chat rate limits | 10 RPM, 60 000 TPM per key | general passthrough + `/responses` |
| Knowledge-path token preflight | **none** (body-size cap only) | knowledge path |
| Knowledge-path outbound `max_tokens` | **not set** (provider default) | knowledge path |

### Retrieval history: last 6 turns, only for query rewriting

`_build_conversation_history` (`app/services/partner_chat.py`) keeps the last
6 prior user/assistant turns and sends them to retrieval-api, which uses only
the **last 3** for coreference resolution (rewriting "how do I cancel *it*?"
into a standalone query). Conversation history never expands what the model
sees on the partner path — the full `messages` array goes to the model as-is.

**Historical landmine (fixed in PR #854):** retrieval-api rejects any
history entry > 8 000 chars with 422 and refuses to truncate
(`retrieval_api/models.py`, REQ-2.6). portal-api's
`_build_conversation_history` originally did **not** clip, so a long
transcript in any of the last 6 prior turns turned into an HTTP 502 on the
knowledge path. Since PR #854 portal-api clips each history entry to 7 800
chars (head + tail with omission marker), mirroring LiteLLM's
`clip_retrieval_history_content` (`klai_kb_request_context.py`). The
passthrough path never had this issue (no retrieval call).

## The only real context management: `deploy/litellm/klai_context.py`

`KlaiContextOrchestrator.assemble()` enforces a per-model **history budget**
by walking messages newest→oldest and dropping the oldest once the budget is
exceeded. Dropping, not summarizing — a placeholder is merged into the system
prompt ("[Earlier conversation turns omitted …]"). The system message and the
last user message are never dropped.

| Alias | Upstream | History budget (tokens / chars) |
|---|---|---|
| klai-primary | mistral-small-2603 | 6 000 / 24 000 |
| klai-fast | mistral-small-2603 | 4 000 / 16 000 |
| klai-large | mistral-large-2512 | 12 000 / 48 000 |
| klai-medium | mistral-medium-3.5 | 8 000 / 32 000 |

Env-overridable via `KLAI_CONTEXT_*_HISTORY_BUDGET_{CHARS,TOKENS}`.

Where it applies (via `custom_router.token_router`):

- **klai-primary requests: always** — including partner passthrough traffic
  (the router does not check the `_klai_openai_passthrough` flag).
- Explicit `klai-fast`/`klai-large`/`klai-medium` requests without a `user`
  field (i.e. partner traffic on those models): **no budget at all** — raw
  messages go to Mistral.

Also in this layer: tool messages before the last user turn are dropped;
active tool results are truncated to the char budget; the router upgrades to
klai-large on tool history or a >300-token last user message, and downgrades
to klai-fast on URL-heavy/search content.

## LibreChat specifics

- `deploy/librechat/librechat.yaml`: `summarize: false` (LibreChat's built-in
  compaction is off), no `maxContextTokens` — LibreChat sends its default
  full history; the LiteLLM budget above is the only guard.
- Tenant provisioning (`provisioning/generators.py`) never touches these
  settings; all tenants inherit them.

## Transcripts

- Scribe summarizes transcripts (two-stage map/reduce in
  `scribe-api/app/services/summarizer.py`) but that output is **not** wired
  into any chat path. The full transcript text (not the summary) can be
  ingested into a KB (`meeting_transcript` doc type) — RAG over transcripts
  via the knowledge path is the supported pattern.
- The only "big document into chat" path is the LibreChat PDF attachment
  handler (`klai_chat_attachments.py`), which **rejects** above 120 000
  extracted tokens rather than chunking or summarizing.

## What does NOT exist (explicit)

1. Server-side conversation store or replay for partner chat.
2. Any `conversation_id`/thread concept on chat requests.
3. Summarization/compaction anywhere in a chat request path.
4. Message/token caps on what the knowledge path forwards to the model
   (128 KB body cap only).
5. Prompt caching on the knowledge path and `/responses`: `prompt_cache_key`
   is rejected on `/responses` and ignored on the knowledge path. On the
   general passthrough it IS supported since PR #855: portal translates the
   OpenAI-style top-level `prompt_cache_key` into LiteLLM `extra_body` so it
   reaches Mistral (cached input bills at 10% of the input price; verified
   live 2026-08-12 — LiteLLM 1.91.0 drops the top-level field for Mistral,
   the `extra_body` route engages caching).
6. Token-based rate limiting on the knowledge path or widget (RPM only).

## Guidance for API partners (current contract)

- Client-side threading is the contract, as with OpenAI/Anthropic/Mistral.
  Keep a sliding window (our own surfaces use 6–80 messages) and summarize
  long artifacts (transcripts) client-side before injecting — or ingest them
  into a KB and use the knowledge path so answers come with sources.
- On the general passthrough, send a stable `prompt_cache_key` per
  conversation and keep prefixes stable (system prompt first, history
  appended): Mistral then bills the unchanged prefix at 10% of the input
  price. See `docs/runbooks/partner-chat-threading.md`.
