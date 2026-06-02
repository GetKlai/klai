# SPEC-SHIELD-001: Klai Shield voor Externe LLMs, API en MCP

**Status:** Proposed
**Priority:** High
**Service:** klai-portal, klai-knowledge-mcp, klai-retrieval-api, klai-shield-extension
**Last updated:** 2026-06-02
**Reference source:** `/Users/jantinedoornbos/Documents/Projects/Superdock`

**Product problem this solves:**

> Use Klai from scripts, Claude Desktop, Cursor, and your own tools.
> Klai is useful in the portal. But a lot of work happens somewhere else:
> a script that prepares a client file, Cursor while you write code, Claude
> Desktop while you work through a decision document. With the Klai API and MCP
> server: search your organisation's knowledge base from outside the portal,
> insert relevant context, and save new knowledge back.

Shield is the compliant outside-Klai layer. It lets users talk to other LLMs and
tools while bringing Klai's knowledge, policy checks, and audit posture with them.

---

## 1. Achtergrond

Superdock Shield is currently mostly a browser-extension experience:

- Chrome MV3 extension intercepts prompts in ChatGPT, Claude, Gemini, Copilot, etc.
- Laravel `/shield/*` routes expose config, RAG query, compliance check, logs, images.
- Python `/query` and `/shield/check` provide retrieval and EU AI Act classification.

Klai already has stronger foundations, but spread across services:

- `klai-portal` exposes Partner API endpoints for scripts and custom tools.
- `klai-knowledge-mcp` exposes MCP tools for Claude Desktop, Cursor, ChatGPT connectors, and LibreChat.
- `klai-retrieval-api` owns tenant-safe hybrid retrieval.
- `knowledge-ingest` owns writes into personal/org/documentation knowledge.
- `portal_orgs.telemetry_level` already defines privacy modes: `off`, `shadow`, `full`.
- Existing app routes use BFF cookies; external tools use API keys or MCP OAuth tokens.

So Klai Shield must not become only "the Chrome extension". The browser extension is one
delivery channel. The core product is:

1. **Compliant chat preparation**: check a prompt before it goes to another LLM.
2. **Knowledge insertion**: search Klai KBs and return context blocks/sources for that LLM.
3. **Knowledge saving**: save useful outputs/decisions back into Klai from external tools.
4. **Audit and governance**: log safety decisions without default raw prompt retention.

---

## 2. Goals

1. Scripts can call Klai through an API to prepare or run compliant, knowledge-grounded LLM calls.
2. Claude Desktop, Cursor and other MCP clients can search Klai knowledge and save new knowledge.
3. Web LLMs without MCP/API integration can use the browser extension for pre-flight checking and context insertion.
4. Shield blocks clearly disallowed prompts before they are sent to a third-party LLM surface.
5. Shield never introduces a second retrieval engine or bypasses Klai's existing tenant identity checks.
6. Shield logs enough for governance and usage insight while honoring tenant telemetry mode.

---

## 3. Non-Goals For MVP

- Becoming a universal enterprise proxy that forcibly intercepts all network traffic.
- Guaranteeing compliance in arbitrary third-party tools that do not call the API/MCP
  and are not used through the browser extension.
- Firefox/Safari extensions.
- Chrome Web Store publication.
- Image asset insertion into external LLMs.
- Full output moderation after every third-party LLM response.
- Direct user-provided non-EU model routing without a separate provider/privacy review.

---

## 4. Surfaces

Shield has three surfaces. They share the same policy concepts, but each uses the
auth mechanism that already fits that client class.

| Surface | Primary users | Auth | Main use |
|---|---|---|---|
| Partner API | scripts, internal tools, custom apps | `pk_live_...` API key | prepare/chat with compliant knowledge grounding; append knowledge |
| MCP server | Claude Desktop, Cursor, ChatGPT connectors | `klai_mcp_...` OAuth token | search/save knowledge from AI workspaces |
| Browser extension | ChatGPT/Claude/Gemini web UI | `ks_live_...` extension token | intercept prompt, check compliance, insert context |

The Partner API and MCP server are the main answer to "use Klai outside the portal".
The browser extension is the fallback for external LLM web apps where Klai cannot
control the host application's API.

---

## 5. Assumptions

1. Partner API keys remain the preferred credential for scripts and custom tools.
2. MCP OAuth remains the preferred credential for Claude Desktop, Cursor and future MCP clients.
3. A separate `ks_live_...` token is only needed for the browser extension, because Klai's
   normal app API intentionally uses BFF cookies and CSRF.
4. Browser, API and MCP calls must all resolve identity server-side; callers must not pass
   trusted `org_id` or `user_id` values directly.
5. Retrieval always flows through `klai-retrieval-api`; no Shield-specific vector search path.
6. Knowledge insertion always flows through existing knowledge-ingest paths.
7. Third-party MCP traffic is privacy-by-default and capped at `shadow` telemetry unless
   a future SPEC explicitly changes that.

---

## 6. User Stories

### US-1: Script uses Klai before calling an LLM

As a developer, I want my script to ask Klai for compliant context before calling an LLM,
so that generated client files include organisation knowledge and avoid obvious policy issues.

### US-2: Claude Desktop searches and saves Klai knowledge

As a user working in Claude Desktop, I want Claude to search my Klai knowledge base and
save decisions back into Klai, so that work outside the portal does not disappear.

### US-3: Cursor uses organisation context

As a user in Cursor, I want to search Klai knowledge while coding or writing docs, so that
answers reflect our actual decisions and customer context.

### US-4: Browser LLM prompt is checked before send

As a user in ChatGPT or Claude web, I want Shield to review and enrich my prompt before it
is sent, so I can use external LLMs without losing Klai's compliance posture.

### US-5: Admin governs outside-portal AI usage

As a tenant admin, I want Shield usage to be visible and configurable, so that external AI
work stays auditable without storing raw prompts by default.

---

## 7. Requirements

### R-1: API Shield Prepare (Event-Driven)

**WHEN** a script calls `POST /partner/v1/shield/prepare` with a prompt and optional
knowledge selection,
**THEN** Klai **shall**:

1. validate the Partner API key and its KB access
2. run compliance checks on the input prompt
3. block red prompts before retrieval or external model forwarding
4. retrieve relevant Klai knowledge when enabled
5. return an enriched prompt/context package with source metadata
6. write privacy-gated Shield audit metadata

**WHEN** the result is `blocked=true`,
**THEN** the response **shall not** include an enriched prompt intended for forwarding
to another LLM.

### R-2: API Shield Chat (Event-Driven)

**WHEN** a script calls `POST /partner/v1/chat/completions` with `shield.enabled=true`,
**THEN** Klai **shall** apply Shield prepare before the model call.

**WHEN** Shield blocks the prompt,
**THEN** Klai **shall** return a structured refusal instead of calling the model provider.

**WHEN** Shield allows the prompt,
**THEN** Klai **shall** inject retrieved knowledge and policy instructions into the model
call using the existing Partner API chat pipeline.

### R-3: MCP Knowledge Search (Ubiquitous)

The existing MCP `search_knowledge` tool **shall** remain the canonical way for Claude
Desktop, Cursor and other MCP clients to read Klai knowledge.

**WHEN** an MCP client calls `search_knowledge`,
**THEN** the MCP server **shall** verify the OAuth token through portal-api and call
retrieval-api with verified identity.

**WHEN** retrieval fails,
**THEN** the MCP tool **shall** fail loudly with a generic user-facing error, not pretend
that no knowledge exists.

### R-4: MCP Knowledge Insert (Ubiquitous)

The existing MCP write tools **shall** remain the canonical way for AI workspaces to save
knowledge:

- `save_personal_knowledge`
- `save_org_knowledge`
- `save_to_docs`

**WHEN** a user asks Claude Desktop/Cursor to remember, save, or add something to Klai,
**THEN** the host LLM should call the matching MCP tool.

**WHEN** the caller lacks role or KB write access,
**THEN** the tool **shall** return a clear user-facing permission error and shall not
forward to knowledge-ingest.

### R-5: MCP Compliance Tools (Event-Driven)

**WHEN** an MCP client needs to check a prompt or generated draft before use,
**THEN** `klai-knowledge-mcp` **shall** expose a Shield compliance tool:

```text
check_compliance(text, level="basic", type="input")
```

It returns:

- score: `green | yellow | orange | red`
- warnings
- blocked boolean
- recommended action

**WHEN** score is `red`,
**THEN** the tool description **shall** instruct the host LLM not to proceed with the
unsafe action and to ask the user to revise.

### R-6: Browser Extension (Event-Driven)

**WHEN** a user submits a prompt in a supported web LLM,
**THEN** the extension **shall**:

1. intercept submit
2. run client-side deterministic checks
3. call server-side `/api/shield/check` when enabled
4. call `/api/shield/query` when active KBs are selected
5. insert retrieved context into the prompt
6. block red prompts
7. log the decision fire-and-forget

**WHEN** retrieval fails or times out,
**THEN** the extension **shall** continue with compliance review instead of blocking
solely because knowledge was unavailable.

### R-7: Knowledge Context Format (Ubiquitous)

**WHEN** Shield returns context for an external LLM,
**THEN** it **shall** use a stable, parseable context shape:

```text
<klai_knowledge_context>
Use the following Klai knowledge as context. Cite the listed sources when relevant.

[1] Source title
Source URL: https://example.com/doc
Excerpt:
...
</klai_knowledge_context>
```

**WHEN** no reliable source URL is available,
**THEN** Shield **shall** include a source label/title and must not invent a URL.

### R-8: Compliance Scoring (Ubiquitous)

Shield **shall** support three policy levels:

- `basic`: PII/GDPR, transparency, synthetic/deepfake labeling
- `extended`: basic plus high-risk domains and emotion recognition
- `strict`: extended plus prohibited AI practices

Allowed scores:

- `green`: no issue found
- `yellow`: low risk, attention recommended
- `orange`: clear warning, user/admin should review
- `red`: blocked

### R-9: Privacy-Gated Audit Logging (Ubiquitous)

Shield logs **shall** follow `portal_orgs.telemetry_level`:

| Mode | Behavior |
|---|---|
| `off` | no Shield prompt audit rows |
| `shadow` | metadata only; no raw prompt preview |
| `full` | metadata plus max 200-character preview; purge/redact after 7 days |

Shield logs **shall not** persist the full prompt, enriched prompt, retrieved context block,
OAuth token, API key, MCP token, or external model response.

### R-10: Admin Governance (State-Driven)

Tenant admins **shall** be able to configure:

- Shield enabled/disabled
- default compliance level
- whether browser extension tokens may be created
- whether Partner API Shield mode is allowed
- recent Shield activity summary

Feature unlocks can reuse `portal_orgs.platform_unlocked_features` if Shield should be
tenant-gated before broad launch.

---

## 8. API Specification

### Existing Partner API: GET /partner/v1/knowledge-bases

Lists KBs a `pk_live_...` key can use. This already supports scripts and should remain
the first discovery call for custom tools.

### Existing Partner API: POST /partner/v1/knowledge

Appends content to a KB when the key has `knowledge_append` and `read_write` access.

Shield requirement: this endpoint becomes the script-side "insert knowledge" path.
No new parallel Shield-specific write sink should be created.

### Extended Partner API: POST /partner/v1/chat/completions

Add optional `shield` object:

```json
{
  "model": "klai-primary",
  "messages": [
    { "role": "user", "content": "Schrijf een klantmail over ons retourbeleid" }
  ],
  "knowledge": {
    "enabled": true,
    "knowledge_base_ids": [7],
    "top_k": 5,
    "include_sources": true
  },
  "shield": {
    "enabled": true,
    "compliance_level": "basic",
    "mode": "block"
  },
  "stream": true
}
```

Behavior:

- `shield.enabled=false` preserves existing Partner API behavior.
- `shield.enabled=true` runs compliance before model call.
- `mode="block"` blocks `red`.
- `mode="warn"` returns warnings but only blocks `red` unless future admin policy says otherwise.

### New Partner API: POST /partner/v1/shield/prepare

For scripts that want Klai to prepare a prompt but still call their own LLM provider.

**Request:**

```json
{
  "prompt": "Maak een concept advies voor klant X",
  "knowledge": {
    "enabled": true,
    "knowledge_base_ids": [7],
    "query": "advies klant X",
    "top_k": 5
  },
  "compliance_level": "extended",
  "include_enriched_prompt": true
}
```

**Response 200:**

```json
{
  "blocked": false,
  "score": "green",
  "warnings": [],
  "enriched_prompt": "<klai_knowledge_context>...</klai_knowledge_context>\n\nMaak een concept advies voor klant X",
  "sources": [
    {
      "title": "Klant X afspraken",
      "source_url": "https://example.com/doc",
      "kb_slug": "customers",
      "score": 0.83
    }
  ],
  "audit_id": "uuid"
}
```

**Response 200 blocked:**

```json
{
  "blocked": true,
  "score": "red",
  "warnings": [
    {
      "severity": "red",
      "article": "GDPR",
      "label": "Personal data detected",
      "description": "The prompt contains personal data."
    }
  ],
  "enriched_prompt": null,
  "sources": [],
  "audit_id": "uuid"
}
```

### New Partner API: POST /partner/v1/shield/check

For scripts that only need compliance scoring.

```json
{
  "text": "Prompt or draft",
  "type": "input",
  "level": "basic"
}
```

Returns `score`, `blocked`, `warnings`, `source`.

---

## 9. MCP Specification

### Existing tools to keep

- `search_knowledge(query, top_k=8)`
- `save_personal_knowledge(title, content, assertion_mode, tags, source_note?)`
- `save_org_knowledge(title, content, assertion_mode, tags, source_note?)`
- `save_to_docs(title, content, kb_name?, page_path?)`

### New tools

#### check_compliance

```text
check_compliance(text, level="basic", type="input")
```

Use when the host LLM is about to draft, transform, forward, or act on text that may
carry compliance risk.

#### prepare_prompt

```text
prepare_prompt(prompt, query?, top_k=8, level="basic")
```

Returns the same semantic shape as `/partner/v1/shield/prepare`, but for MCP hosts.
This gives Claude Desktop/Cursor a single tool call that both checks compliance and
retrieves Klai knowledge.

The MCP tool description must strongly instruct host LLMs:

- call `prepare_prompt` before answering org-specific or compliance-sensitive work
- do not proceed when `blocked=true`
- cite returned sources when using returned context
- call save tools when the user explicitly asks to remember/save/share knowledge

---

## 10. Browser Extension Specification

The browser extension is still useful, but it is now a Shield channel rather than the
whole product.

### MVP behavior

- Supported sites: ChatGPT, Claude, Gemini, Copilot, Mistral, Poe, Perplexity.
- Local settings: enabled, compliance level, active KBs, active template.
- API base: tenant portal origin.
- Auth: `ks_live_...` extension token, user-bound and revocable.
- Prompt flow: intercept, check, retrieve, insert context, block/warn/send.

### Extension endpoints

- `POST /api/app/shield/tokens` create extension token through BFF session
- `GET /api/app/shield/tokens` list own tokens
- `DELETE /api/app/shield/tokens/{id}` revoke own token
- `GET /api/shield/config`
- `POST /api/shield/query`
- `POST /api/shield/check`
- `POST /api/shield/log`

These endpoints are for the browser extension only. Scripts should use Partner API.
MCP clients should use MCP OAuth.

---

## 11. Data Model

### New table: `portal_shield_settings`

```sql
CREATE TABLE portal_shield_settings (
    org_id                   INTEGER PRIMARY KEY REFERENCES portal_orgs(id) ON DELETE CASCADE,
    enabled                  BOOLEAN NOT NULL DEFAULT true,
    partner_api_enabled      BOOLEAN NOT NULL DEFAULT true,
    mcp_enabled              BOOLEAN NOT NULL DEFAULT true,
    browser_extension_enabled BOOLEAN NOT NULL DEFAULT true,
    compliance_enabled       BOOLEAN NOT NULL DEFAULT true,
    default_level            TEXT NOT NULL DEFAULT 'basic'
        CHECK (default_level IN ('basic', 'extended', 'strict')),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by               TEXT NULL
);
```

### New table: `portal_shield_logs`

```sql
CREATE TABLE portal_shield_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    user_id         TEXT NULL,
    principal_type  TEXT NOT NULL CHECK (principal_type IN ('user', 'partner_api_key', 'mcp_client')),
    principal_id    TEXT NULL,
    surface         TEXT NOT NULL CHECK (surface IN ('partner_api', 'mcp', 'browser_extension')),
    action          TEXT NOT NULL CHECK (action IN ('check', 'prepare', 'chat', 'query', 'knowledge_append')),
    check_type      TEXT NULL CHECK (check_type IN ('input', 'output')),
    level           TEXT NULL,
    score           TEXT NULL CHECK (score IN ('green', 'yellow', 'orange', 'red')),
    platform        TEXT NULL,
    text_preview    TEXT NULL,
    warnings        JSONB NOT NULL DEFAULT '[]',
    sources         JSONB NOT NULL DEFAULT '[]',
    was_blocked     BOOLEAN NOT NULL DEFAULT false,
    was_overridden  BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_portal_shield_logs_org_created
    ON portal_shield_logs (org_id, created_at DESC);
```

### New table: `portal_shield_tokens`

Only for browser extension tokens.

```sql
CREATE TABLE portal_shield_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL,
    name          VARCHAR(128) NOT NULL,
    token_prefix  VARCHAR(16) NOT NULL,
    token_hash    VARCHAR(64) NOT NULL UNIQUE,
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ NULL,
    last_used_at  TIMESTAMPTZ NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Partner API keys and MCP OAuth tokens remain in their existing tables.

---

## 12. Implementation Plan

### Milestone 1: Partner API Shield mode

Files:

- `klai-portal/backend/app/api/partner.py`
- `klai-portal/backend/app/services/shield_compliance.py` (new)
- `klai-portal/backend/app/services/shield_prepare.py` (new)
- `klai-portal/backend/app/models/shield.py` (new)
- Alembic migration

Tasks:

1. Add `shield` option to `ChatCompletionsRequest`.
2. Add `/partner/v1/shield/check`.
3. Add `/partner/v1/shield/prepare`.
4. Reuse `retrieve_context` and Partner API KB access validation.
5. Add privacy-gated `portal_shield_logs` writer.

### Milestone 2: MCP Shield tools

Files:

- `klai-knowledge-mcp/main.py`
- `klai-knowledge-mcp/tests/`

Tasks:

1. Add `check_compliance`.
2. Add `prepare_prompt`.
3. Keep existing `search_knowledge` and save tools as canonical read/write tools.
4. Add tests for blocked red, shadow telemetry, OAuth identity, and generic failure messages.

### Milestone 3: Browser extension channel

Files:

- `klai-shield-extension/` (new)
- `klai-portal/backend/app/api/shield.py` (new)
- `klai-portal/backend/app/services/shield_tokens.py` (new)

Tasks:

1. Port Superdock extension as source material.
2. Rename user-facing strings to Klai Shield.
3. Implement extension token lifecycle.
4. Implement `/api/shield/config/query/check/log` facade.
5. Verify prompt interception in mock pages before trying live AI sites.

### Milestone 4: Admin and docs

Tasks:

1. Add Shield settings to `/admin/settings`.
2. Add extension token section to `/app/account`.
3. Add docs for:
   - scripts using `/partner/v1/shield/prepare`
   - Partner API chat with `shield.enabled=true`
   - Claude Desktop/Cursor MCP setup
   - browser extension install

---

## 13. Test Plan

### Partner API

- `shield.enabled=false` preserves existing chat behavior.
- `shield.enabled=true` blocks red prompts before model call.
- `/shield/prepare` validates KB access and never returns cross-tenant context.
- `/shield/prepare` returns no `enriched_prompt` when blocked.
- `/partner/v1/knowledge` remains the write path and requires `knowledge_append`.

### MCP

- `search_knowledge` still returns chunks for valid OAuth token.
- `check_compliance` returns `blocked=true` for deterministic red cases.
- `prepare_prompt` returns context plus warnings.
- Save tools still enforce role gates.
- Failure responses do not leak internal secrets, URLs, or upstream body.

### Browser extension

- Client-side PII checks catch email, Dutch phone, IBAN and valid BSN.
- Red prompt is blocked.
- Orange/yellow prompt requires conscious override.
- Retrieval timeout does not block compliance flow.
- Badge and review modal fit at side-panel widths.

### Privacy

- `off`: no Shield log row.
- `shadow`: log row has null `text_preview`.
- `full`: log row has max 200 chars and is purge/redaction eligible.

---

## 14. Acceptance Criteria

1. **Given** a Partner API key with chat access, **when** a script calls chat with
   `shield.enabled=true`, **then** Klai checks the prompt before calling the model.
2. **Given** a red compliance result, **when** Shield runs on API, MCP or extension,
   **then** the unsafe action is blocked before external forwarding.
3. **Given** Claude Desktop with Klai MCP connected, **when** the user asks an
   org-specific question, **then** the host can call `search_knowledge` and receive
   source-backed Klai context.
4. **Given** a user asks Claude Desktop to save a decision, **when** the host calls a
   save tool, **then** the content is inserted into the correct Klai knowledge scope.
5. **Given** a script calls `/partner/v1/shield/prepare`, **then** it receives either
   an enriched prompt with sources or a blocked response with warnings.
6. **Given** `telemetry_level=shadow`, **then** no Shield surface stores raw prompt text.
7. **Given** an inaccessible KB, **then** API/MCP/extension retrieval fails authorization
   before retrieval-api is called.

---

## 15. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| MCP host ignores tool instructions | Compliance is advisory in Claude Desktop/Cursor | Make tool descriptions explicit; browser extension/API provide hard enforcement where Klai controls flow |
| Partner API becomes too many endpoints | Confusing developer UX | Keep existing `/chat/completions` and `/knowledge`; add only `/shield/check` and `/shield/prepare` |
| Cross-tenant KB leak | Severe security incident | Reuse Partner API/MCP identity validation and KB access helpers; retrieval-api stays internal |
| Raw prompt retention drift | Privacy regression | Central Shield log writer gated by `telemetry_level`; tests for every mode |
| Browser extension DOM drift | Extension breaks on AI sites | Isolate selectors per platform and test against mock pages |
| Non-EU third-party LLM routing | Privacy/product mismatch | MVP only prepares prompts or uses Klai-approved model aliases; external provider forwarding needs separate review |

---

## 16. Open Questions

1. Should Shield be a platform unlock before launch, or enabled for all paid tenants?
2. Should `/partner/v1/shield/prepare` return the full enriched prompt by default, or require
   `include_enriched_prompt=true` for extra caution?
3. Should MCP `prepare_prompt` be one tool, or should compliance and retrieval remain separate
   to reduce host-model confusion?
4. Should `shield.enabled=true` become the default for new Partner API keys?
5. Is browser extension MVP still needed before API/MCP launch, or can it be phase 2?
