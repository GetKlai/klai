---
id: SPEC-SLACK-001
version: "0.1.0"
status: approved for implementation
created: 2026-10-28
updated: 2026-10-28
author: Claude (Fable), commissioned by Mark Vletter
priority: high
related:
  - SPEC-API-001 (Partner API: pk_live_ keys, KB whitelist, /chat/completions knowledge extension)
  - SPEC-WIDGET-002 (managed credentials with KB access tables; Cat-B RLS on partner_api_keys)
  - SPEC-PORTAL-EXTENSIONS-UNIFY-001 (platform unlocks; KNOWN_FEATURES registry)
  - SPEC-SEC-ENVFILE-SCOPE-001 (compose env scoping for the new service)
  - SPEC-SEC-MAILER-INJECTION-001 (shared-secret pattern between services)
  - docs/architecture/chat-system.md (the one chat path every surface uses)
---

# HISTORY

| Version | Date       | Author | Change |
|---------|------------|--------|--------|
| 0.1.0   | 2026-10-28 | Claude (Fable), commissioned by Mark Vletter | Initial design after a research round (Partner API vs MCP OAuth, Slack bot frameworks, portal and Slack UX). Decisions taken by Mark: build on the Partner API key, admin configures once, the three-layer knowledge-base governance. |

---

# SPEC-SLACK-001: Klai in Slack

## 1. Summary

Customers want to ask Klai questions from Slack: in a direct message with the
bot, by @-mentioning it in a channel, and in Slack's agent surface (the app's
Messages tab). Every answer must be grounded in the customer's knowledge bases,
show which knowledge bases were searched and cite its sources, exactly like the
widget and the internal chat do today.

The integration is a new service, `klai-slack`, that speaks Slack on one side
(Bolt for Python over the HTTP Events API) and Klai's existing Partner API on
the other (`POST /partner/v1/chat/completions` with the `knowledge` extension).
Nothing in the answer pipeline changes: a Slack turn is a partner turn. The
customer's admin configures the integration once in the portal; individual
Slack users never authenticate against Klai.

## 2. Decisions (taken, do not reopen)

| # | Decision | Why |
|---|---|---|
| D1 | Access is a **Partner API key** per Slack workspace, minted by the portal as a *managed* key (`permissions.slack = true`). Not MCP OAuth per user. | Zero friction per user; the key model already carries the KB whitelist; the MCP server returns passages, not answers, so a per-user route would need a second answer pipeline. Per-user identity stays an upgrade path (§11). |
| D2 | One Slack workspace per Klai organisation, one organisation per workspace. | Keeps the portal page a single card instead of a list; enforced by unique constraints both ways. |
| D3 | Knowledge-base governance has three layers (§5). "All allowed KBs" is the default at every layer. | Admin keeps the maximum; channel owners and users only narrow. Retrieval already searches several KBs at once, so "all" is a safe default. |
| D4 | Framework: **slack-bolt (Python) 1.30 + slack_sdk 3.45**, async, mounted in FastAPI. No ready-made bot, no no-code tool for production. | Only maintained option with HTTP Events API, OAuth installation store, modals, `agent_view` and streaming. Every ready-made OpenAI-compatible bot found is Socket-Mode, single-workspace and cannot inject the `knowledge` field. **New dependencies, approved by Mark with the go for implementation on 2026-10-28.** |
| D5 | Transport: HTTP Events API behind Caddy (`https://api.getklai.com/slack/*`). Socket Mode only for local development. | Slack recommends HTTP for production; Socket Mode apps cannot be listed in the Marketplace and are capped at 10 connections. |
| D6 | Build on Slack's **`agent_view`**, not the deprecated `assistant_view` / Bolt `Assistant` class. | `assistant_view` is retired in February 2027; the migration is one-way. |
| D7 | The bot keeps its **own transcript per thread** (the question/answer pairs it handled) and calls `conversations.replies` only when it meets a thread it has not seen. | Since 2025-05-29 Slack throttles `conversations.replies` to 1 req/min for distributed non-Marketplace apps; the bot receives every question as an event anyway. Verified in the spike of §10 before the behaviour is relied on. |
| D8 | Separate service with its own Postgres schema; the portal talks to it only over an internal HTTP API with a shared secret. | Same shape as klai-connector and klai-mailer; the portal backend stays the portal's own. |
| D9 | Onboarding is **portal-driven Slack OAuth**: the admin picks knowledge bases and clicks "Verbind met Slack"; the key is minted server-side after Slack's consent and handed to klai-slack (§8). The paste-the-key route (`/klai connect`) shipped first on 2026-10-28 and was removed the same day (one test link, nothing to migrate). | The founder judged the paste flow unnatural on the live page the same day; a product feature ships on the better path, not the least-code path. |
| D10 | Feature gated per workspace owner by a **new platform unlock `slack`**, not `partner_api`. | `partner_api` means "the workspace owner builds integrations with keys"; Slack is a finished product. A `slack` managed key skips the `partner_api` gate and is checked against `slack` instead, mirroring the `internal_chat` key. |
| D11 | KB choices live in klai-slack's Postgres, never in Slack message metadata. Every turn intersects the stored choice with the live allowed set. | Message metadata is readable by every workspace member; the intersection makes a revocation in the portal effective immediately. |

## 3. Architecture

```
Slack workspace ──events/commands/interactivity──▶ Caddy /slack/* ──▶ klai-slack (Bolt in FastAPI)
                                                                          │
                                                                          │ Bearer pk_live_… (per workspace)
                                                                          ▼
                                                               portal-api /partner/v1/{chat/completions,knowledge-bases,feedback}
klai-slack ◀──internal API (shared secret)──▶ portal-api /internal/slack/*  (link, unlink)
portal-api ──internal API (shared secret)──▶ klai-slack /internal/installations/* (disconnect)
Portal admin UI /admin/slack ──▶ portal-api /api/admin/slack
```

Components and owners:

| Component | Repo path | Owns |
|---|---|---|
| `klai-slack` service | `klai-slack/` (new) | Slack installations (OAuth store), link to a Klai organisation + key, channel defaults, thread scopes, transcript, event dedupe, all Slack UX |
| Portal backend | `klai-portal/backend` | The managed `slack` key, the `slack` unlock, `portal_slack_installations` (organisation ↔ team), admin and internal endpoints |
| Portal frontend | `klai-portal/frontend` | `/admin/slack` page, nav item, i18n |
| Deploy | `deploy/`, `.github/workflows/` | Compose service, Caddy route, image build, env scoping |
| Secrets | `klai-infra` (private) | SOPS entries for the new env vars (§7) |

## 4. The Partner API as used by klai-slack

All calls carry `Authorization: Bearer <pk_live_…>` of the workspace's key.

- `GET /partner/v1/knowledge-bases` → `[{id, name, slug, access_level}]`. Used on connect (validation) and whenever a KB list is shown (cached ≤ 60 s per team).
- `POST /partner/v1/chat/completions` with
  ```json
  {"model": "klai-primary", "stream": false,
   "messages": [{"role":"user","content":"…"}, {"role":"assistant","content":"…"}, …],
   "knowledge": {"knowledge_base_ids": [1, 2], "include_sources": true}}
  ```
  `knowledge_base_ids` is omitted (not sent as `[]`) when the effective scope is "all allowed". The non-streaming body is an OpenAI chat completion; `choices[0].message.content` is the answer text, `choices[0].message.sources` is the list of source objects (document-level: title, url, …; read `app/services/answer_compose.py` for the exact keys), `id` is the completion id. A 403 `permission_error` means the scope fell outside the key; a 429 carries `Retry-After`.
- `POST /partner/v1/feedback` with `{"message_id": "<completion id>", "conversation_id": "slack:<team>:<channel>:<thread_ts>", "rating": "thumbsUp"|"thumbsDown", "text": "<optional reason>"}`. Requires the key's `feedback` permission (the managed key has it).

The key's permissions are `{"slack": true, "chat": true, "feedback": true, "knowledge_append": false, "general_chat": false}`; KB access rows are `read` on org KBs only. `rate_limit_rpm` is 600 (the schema maximum; a whole workspace shares one key).

## 5. Knowledge-base governance

| Layer | Who | Where | Stored | Enforced by |
|---|---|---|---|---|
| 1 Maximum | Klai admin | Portal `/admin/slack`, Kennisbanken tab | `partner_api_key_kb_access` rows of the managed key | portal-api `validate_kb_access` (403 outside scope) |
| 2 Channel default | Slack workspace admin/owner | `/klai kb` in the channel | klai-slack `channel_defaults(team_id, channel_id) → kb_ids` | klai-slack intersects with layer 1 per turn |
| 3 Thread scope | Any user | "Kennisbanken kiezen" button on an answer | klai-slack `thread_scopes(team_id, channel_id, thread_ts) → kb_ids` | klai-slack; options limited to the channel's effective set (a thread narrows, never widens) |

Effective scope of a turn = layer 3 if set, else layer 2 if set, else all of layer 1; always intersected with the live layer-1 list. An empty intersection is an error state ("kies opnieuw"), never a silent widening. Personal KBs never appear in layer 1: the portal rejects them when the key is created or patched.

## 6. Data model

### 6.1 klai-slack (own Postgres database `klai_slack`, Alembic)

- Bolt's SQLAlchemy OAuth tables (`slack_installations`, `slack_bots`, `slack_oauth_states`) via `AsyncSQLAlchemyInstallationStore`; `bot_token` and `bot_refresh_token` are encrypted at rest with AES-256-GCM (store subclass), key from `SLACK_ENCRYPTION_KEY` (base64 32 bytes, validated at startup).
- `workspace_links(team_id PK, org_id int, org_name, klai_api_key_enc, key_id, linked_by_slack_user_id, linked_at, revoked_at)`.
- `channel_defaults(team_id, channel_id, kb_ids int[], set_by, updated_at; PK team_id+channel_id)`.
- `thread_scopes(team_id, channel_id, thread_ts, kb_ids int[], set_by, updated_at; PK team_id+channel_id+thread_ts)`.
- `turns(id, team_id, channel_id, thread_ts, message_ts, role 'user'|'assistant', text, completion_id, kb_ids, created_at)`: the bot's own transcript (D7); purge rows older than 30 days nightly, delete all rows of a team on disconnect.
- `processed_events(event_id PK, received_at)`: dedupe for Slack retries; purge after 24 h.

### 6.2 portal-api

- `portal_slack_installations(id, org_id UNIQUE, team_id UNIQUE, team_name, partner_api_key_id FK partner_api_keys ON DELETE CASCADE, installed_by_slack_user_id, installed_at, revoked_at)`. Cat-D RLS (policy in a `post_deploy_*.sql`), added to `RLS_DML_TABLES` and to the explicit delete list of `_finalize_postgres_delete` in deprovisioning.
- `KNOWN_FEATURES` gains `"slack"` (not a product feature).
- The managed key: `permissions.slack = true`. `admin_api_keys.py` hides and rejects it exactly as it does `internal_chat` (generalise `_is_internal_chat` → managed-key check). `partner_dependencies.get_partner_key` skips the `partner_api` unlock for it and asserts `slack` instead.

## 7. Service contracts

### 7.1 Portal admin API (OIDC session, admin role, `require_platform_unlocked("slack")`)

| Method, path | Body | Response |
|---|---|---|
| `GET /api/admin/slack` | – | `200 {status: "not_connected"\|"connected"\|"revoked", key: {id, key_prefix, rate_limit_rpm, last_used_at, created_at} \| null, installation: {team_id, team_name, installed_by_slack_user_id, installed_at, revoked_at} \| null, kb_ids: int[]}` |
| `POST /api/admin/slack/connect` | `{kb_ids: int[] (≥1, shared KBs only, never personal)}` | `200 {connect_url: "https://api.getklai.com/slack/connect", token: "<jwt>"}`: the page form-POSTs `token` (`application/x-www-form-urlencoded`) to `connect_url`, never in a URL; the token is HS256, 15 min, single-use (`jti`), bound to org, user, kb_ids and return URL; mints nothing. `409` when an installation row exists; a key without one (orphan) is deleted and replaced; `400` on a personal or foreign KB |
| `PATCH /api/admin/slack` | `{kb_ids: int[] (≥1)}` | `200` same shape as GET |
| `DELETE /api/admin/slack` | – | deletes installation row + key (+ kb access); calls klai-slack `DELETE /internal/installations/{team_id}` when linked; `204`, or `200 {slack_revoked: false}` when klai-slack could not be reached (loud, not silent) |

Status derivation: no key or no installation row → `not_connected`; row with `revoked_at` → `revoked`; otherwise `connected`.

### 7.2 Portal internal API (klai-slack → portal-api, `Authorization: Bearer <INTERNAL_SECRET>`, same guard as the other `/internal/*` routes)

| Method, path | Body | Response |
|---|---|---|
| `POST /internal/slack/link-oauth` | `{token, team_id, team_name, slack_user_id}` | `200 {org_id, org_name, key_id, kb_ids, api_key, return_url}` (the key is minted here and leaves the portal this once); `401` on an invalid or expired token (one generic message); `403 {detail: {error_code: "feature_not_unlocked"}}` when the organisation lacks the `slack` unlock; `403 {code: "not_admin"}` when the token's user is no longer an active admin of the org (suspended, offboarded, demoted); `409 {code: "token_used"\|"org_already_linked"\|"team_already_linked"}`. The `jti` is claimed before any check, so a refused token cannot be retried |
| `DELETE /internal/slack/link/{team_id}` | – | marks `revoked_at`; `204` (idempotent) |

### 7.3 klai-slack internal API (portal-api → klai-slack, `Authorization: Bearer <INTERNAL_SECRET>`)

| Method, path | Response |
|---|---|
| `DELETE /internal/installations/{team_id}` | revokes the Slack bot token (`auth.revoke`), deletes the link, defaults, scopes, turns; `204` (idempotent) |
| `GET /health` | `200` |

Both directions use the one cross-service secret already shared by portal-api and the mailer (`PORTAL_API_INTERNAL_SECRET`), compared with `hmac.compare_digest`; a missing configured or supplied secret fails closed.

### 7.4 Slack app (manifest)

- `features`: `bot_user` (display name "Klai"), `app_home` (home tab, messages tab, not read-only), `agent_view` (description ≤ 300 chars, no static `suggested_prompts`: they block the per-language prompts set on `app_home_opened` with `static_prompts_configured`), `slash_commands`: `/klai` with usage hint `kb | help`.
- Bot scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:write`, `channels:history`, `groups:history`, `commands`, `users:read`, `assistant:write`.
- Events: `app_mention`, `message.im`, `app_home_opened`, `app_uninstalled`, `tokens_revoked`.
- Interactivity on. Request URL for everything: `https://api.getklai.com/slack/events`; OAuth redirect `https://api.getklai.com/slack/oauth_redirect`; install `https://api.getklai.com/slack/install`.

### 7.5 Environment of klai-slack (compose, explicit `environment:` block, SOPS in klai-infra)

`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`, `SLACK_ENCRYPTION_KEY` (base64 32 bytes), `DATABASE_URL`, `PORTAL_API_URL` (`http://portal-api:8010`), `PORTAL_INTERNAL_SECRET` and `INTERNAL_SECRET` (both `${PORTAL_API_INTERNAL_SECRET}`), `PUBLIC_BASE_URL` (`https://api.getklai.com`), `KLAI_ENV`. Every secret has a startup validator (fail closed, fail loud). Env var in SOPS first, validator second.

## 8. Portal UX (`/admin/slack`)

Sidebar item between Widgets and API-keys, `minRole: 'admin'`, `requiresFeature: 'slack'`, icon `Hash`; the Slack logo as a local SVG on the page. Locked state reuses the `gaps_unlock_required` pattern. A failing klai-slack or portal call shows `QueryErrorState`, never "not connected".

States:
1. **Niet verbonden**: intro (two sentences), org-KB checklist (reuse the widgets `KbAccessEditor`, moved to `components/admin/OrgKbChecklist.tsx`), button "Verbind met Slack" enabled at ≥ 1 KB (form-POSTs the link token, §7.1).
2. **Verbonden**: header with workspace name and badge; tabs Overzicht (workspace, team-id, geïnstalleerd door/op, sleutelprefix, laatst gebruikt, rate limit), Kennisbanken (checklist + Opslaan), Ontkoppelen (`InlineDeleteConfirm`). Kanalen tab (read-only channel defaults) is target state.
3. **Ontkoppeld in Slack** (`revoked`): alert with "Opnieuw koppelen" (delete + recreate key).
4. Errors: already linked elsewhere (never names the other org), no KBs left, partial disconnect ("Verwijder de app ook in Slack").

Implemented connect flow: "Verbind met Slack" calls `POST /api/admin/slack/connect`, which returns the klai-slack `connect_url` and a signed 15-minute single-use link token (org, user, kb_ids, return_url). The browser hands the token to klai-slack with a top-level form POST to `/slack/connect` (never in a URL); klai-slack parks it under a Bolt OAuth state and sends the admin to Slack. On Slack's callback klai-slack exchanges the token at `POST /internal/slack/link-oauth`, where the portal re-checks the unlock, the knowledge bases and that the user is still an active admin, mints the managed key and records the installation; klai-slack stores the key encrypted and returns the browser to `return_url?slack=connected|denied|already_linked|failed`. Caddy drops any `token` query parameter from its access logs as defence in depth.

## 9. Slack UX

**Principle.** Klai in Slack answers only from the knowledge bases the organisation released, always shows what it searched and where the answer comes from, treats "staat er niet in" as a full answer, never reads along or speaks unprompted, always replies in the thread, and configures itself in modals or ephemeral messages.

**Answer message** (every surface): `markdown` block with the answer (not `section`: 3000-char cap), `context` "Gezocht in: *KB a*, *KB b*" (also on refusals), `section` with the top-3 sources as numbered `<url|title>` links, `actions` with "Kennisbanken kiezen" and "Alle n bronnen" (modal), `context_actions` with `feedback_buttons` "Nuttig"/"Niet nuttig" (value `up:<completion_id>`); `text` fallback ≤ 4000 chars; `unfurl_links: false`. Clarifying questions and contact suggestions drop the sources block. Over 12 000 chars: split on paragraphs into follow-up messages, sources and feedback only on the last.

**Surfaces.**
- *Agent container* (Messages tab, paid Slack plan): on `app_home_opened` with `tab=messages` set 4 suggested prompts; on `message.im` set `agents.sessions.setStatus processing`, answer, set `active`, `agents.sessions.rename` with a short title after the first answer. If the sessions API fails (free plan) remember per team and fall back to the DM path.
- *DM and @mention* (any plan): placeholder "Ik zoek in n kennisbanken…", then `chat.update` with the answer; reply in `thread_ts = event.thread_ts ?? event.ts`, never broadcast. Follow-ups in a channel thread need a new @mention in the MVP.
- *`/klai kb`*: `views.open` within 3 s with a loading view, then `views.update` with a `multi_static_select` of the layer-1 KBs (hint "Leeg laten = alle vrijgegeven kennisbanken"); Slack admins and owners only (`users.info`); `view_submission` re-validates against layer 1. The same modal with `callback_id: thread_kb` serves the thread picker, options limited to the channel's effective set.
- *`/klai help`*: ephemeral explanation plus the current scope of this channel. Linking happens only from the portal (§8); there is no `/klai connect`.
- *App Home*: status ("Verbonden met Klai-organisatie X" or "Nog niet gekoppeld"), allowed KBs with a link to the portal, channel defaults as `<#C…>` lines, help, disclaimer that answers can be wrong.
- *Feedback*: `feedback_buttons` → `block_actions` → `POST /partner/v1/feedback`; a thumbs-down opens a small reason modal. Reaction-based feedback is target state.
- *Errors*: not linked, no KBs released, empty intersection, 429 (show `Retry-After`, a "Opnieuw proberen" button, no automatic retry), 5xx/timeout (with request id), bot not in channel (`/invite @Klai`). Every Slack event is acked within 3 s, processed in a background task and deduplicated on `event_id`.

**MVP vs later.** MVP: status + one complete message, every surface above, NL with EN fallback from `users.info` locale. Later: streaming (`chat.startStream` fed by the Partner API SSE, `chat.stopStream` with blocks), `app_context_changed`, follow-ups without mention, reaction feedback, channel-owner check for `/klai kb`.

## 10. Security and operations

- Signing secret verification by Bolt; event dedupe; 3-second ack.
- Secrets compared with `hmac.compare_digest`; empty configured or supplied secret never authenticates.
- Tokens and the Klai key encrypted at rest (AES-256-GCM); the key is never logged, never echoed back to Slack after connect.
- klai-slack is NOT on the `socket-proxy` network and has no Docker access.
- Logging: structlog JSON, `X-Request-ID` propagated to portal-api, no message bodies in logs.
- Spike before relying on D7: measure `conversations.replies` limits on a fresh install of our app; test `feedback_buttons` inside `chat.stopStream` blocks; capture the exact error code of the sessions API on a free plan.

## 11. Upgrade paths (not in scope)

- Per-user identity without per-user login: map the Slack user's e-mail (`users:read.email`) to a Zitadel subject and call the internal-chat profile (`permissions.internal_chat` + `user` field + `X-Klai-Conversation-Id`), which gives personal KBs and role-based scope. Requires an e-mail → subject lookup and makes Slack an identity source; decide when a customer asks for personal KBs in Slack.
- Per-user MCP OAuth link via the existing authorization server, if answers over a user token ever get an endpoint on the chat path.
- Microsoft Teams: keep `PartnerClient` and the KB configuration free of Slack types so a Teams adapter is one thin layer.

## 12. Acceptance tests

1. "Verbind met Slack" plus Slack consent links the workspace; portal GET shows `connected` with the team name; the key is stored encrypted (no plaintext in the DB dump of the test).
2. An invalid, expired or replayed link token, an org without the `slack` unlock, a user no longer an active admin, or an org or workspace already linked each returns to the portal with the mapped `?slack=` outcome and no row written.
3. A DM question returns one message with answer, "Gezocht in" line listing the layer-1 KBs, ≤ 3 sources and feedback buttons; the Partner API request omitted `knowledge_base_ids`.
4. After `/klai kb` selects KB 2 in channel C, an @mention in C sends `knowledge_base_ids: [2]`; a thread scope of KB 1 in a thread of C is rejected because 1 is outside the channel's effective set.
5. Removing KB 2 from layer 1 in the portal makes the next turn in C fall back to the remaining allowed KBs; a channel default that becomes empty yields the "kies opnieuw" message and no Partner API call.
6. The managed key is absent from `GET /api/admin/api-keys` and `PATCH`/`DELETE`/rotate on it return 403; `POST /api/admin/api-keys` with `permissions.slack` is rejected.
7. A key with `permissions.slack` authenticates on `/partner/v1/*` without the `partner_api` unlock but not without the `slack` unlock.
8. Disconnect from the portal deletes key, KB rows and installation row, calls klai-slack, and klai-slack revokes the token and deletes defaults, scopes and turns; a failing klai-slack is reported as `{slack_revoked: false}`.
9. A Slack retry (same `event_id`, `X-Slack-Retry-Num: 1`) produces no second answer.
10. Tenant deprovisioning of an org with a Slack installation completes (FK list) and leaves no `portal_slack_installations` row.

## 13. Implementation slices

| Slice | Executor | Files | Depends on |
|---|---|---|---|
| A1 Service core | Opus | `klai-slack/`: pyproject, Dockerfile, config + validators, logging, models + Alembic, crypto, OAuth store subclass, `PartnerClient`, `PortalClient`, Bolt wiring in FastAPI, `/klai connect`, internal API, tests | – |
| A2 Conversation | Opus | `klai-slack/`: mention/DM/agent handlers, answer rendering, `/klai kb` + thread picker, `/klai help`, App Home, feedback, errors, dedupe, transcript, tests | A1 |
| B Portal backend | Opus | registry, managed key, `partner_dependencies`, `admin_slack.py`, `internal_slack.py`, model + Alembic + post-deploy RLS SQL, deprovisioning delete list, tests | – |
| C Portal frontend | Sonnet | `routes/admin/slack/*`, `-nav.ts`, `components/admin/OrgKbChecklist.tsx`, messages nl/en, tests, `npm run build` | – (contract §7.1) |
| D Deploy | Sonnet | compose service, Caddy `/slack/*`, env-scope-guard, `klai-slack.yml` workflow, audit scripts, runbook `docs/runbooks/slack-integration.md` | A1 |
| E Secrets + rollout | Mark + Claude | SOPS entries in klai-infra, Slack app creation from the manifest, `slack` unlock for the pilot workspace, spike of §10 | D |
