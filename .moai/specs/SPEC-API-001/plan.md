# Implementation Plan: SPEC-API-001 — Partner API

## Overview

Build a Partner API layer in portal-api that enables external parties to integrate their own chat clients with Klai's knowledge layer. The API provides OpenAI-compatible chat completions (with RAG under the hood), knowledge management, and feedback — all scoped to specific knowledge bases via API keys.

## Task Decomposition

### Task 1: PartnerAPIKey Data Model & Migration

**Files to create/modify:**
- `klai-portal/backend/app/models/partner_api_keys.py` (new model)
- `klai-portal/backend/app/models/__init__.py` (register model)
- Alembic migration (new)

**Model: PartnerAPIKey**
| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| org_id | Integer | FK to portal_orgs, RLS-scoped |
| name | String(128) | Human-readable key name |
| key_prefix | String(8) | First 8 chars of key (for identification, e.g., `pk_live_`) |
| key_hash | String(64) | SHA-256 hash of the full key |
| permissions | JSONB | `{"chat": true, "knowledge_read": true, "knowledge_write": false, "feedback": true}` |
| rate_limit_rpm | Integer | Requests per minute (default: 60) |
| allowed_kb_ids | ARRAY(UUID) | List of KB IDs this key can access |
| active | Boolean | Enable/disable key |
| last_used_at | DateTime | Updated on each request |
| created_at | DateTime | Auto-set |
| created_by | UUID | Portal user who created the key |

**Key generation:** `pk_live_` + 40 random hex chars. Only shown once at creation. Stored as SHA-256 hash.

**RLS:** Same org_id-based policy as other portal tables.

### Task 2: API Key Auth Middleware

**Files to create/modify:**
- `klai-portal/backend/app/api/partner_dependencies.py` (new)

**Dependency: `get_partner_key()`**
- Extract `Authorization: Bearer pk_...` header
- SHA-256 hash the key
- Lookup in `partner_api_keys` table
- Verify `active=True`
- Update `last_used_at`
- Return `PartnerAPIKey` instance with resolved org context
- Rate limit check against `rate_limit_rpm` (Redis-based sliding window)

**Error responses:**
- 401: Invalid or missing API key
- 403: Key inactive or permission denied
- 429: Rate limit exceeded

### Task 3: Chat Completions Endpoint

**Files to create/modify:**
- `klai-portal/backend/app/api/partner.py` (new router)
- `klai-portal/backend/app/services/partner_chat.py` (new service)

**Endpoint: `POST /partner/v1/chat/completions`**

Request (OpenAI-compatible):
```json
{
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is our refund policy?"}
  ],
  "model": "klai-primary",
  "stream": true,
  "temperature": 0.7,
  "knowledge_bases": ["kb-uuid-1", "kb-uuid-2"]  // optional: override key's default KBs
}
```

Response (OpenAI-compatible SSE streaming):
```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"role":"assistant","content":"Based on"},"index":0}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":" the refund policy..."},"index":0}]}
data: [DONE]
```

**Flow:**
1. Validate API key via `get_partner_key()` dependency
2. Check `permissions.chat == true`
3. Validate requested `knowledge_bases` are subset of key's `allowed_kb_ids`
4. Call retrieval-api with org_id + scoped KB IDs + user query
5. Build system prompt with retrieved context chunks
6. Stream via httpx to LiteLLM (`http://litellm:4000/v1/chat/completions`)
7. Forward SSE stream to client
8. Log retrieval context for feedback correlation (async, non-blocking)

**Model restriction:** Only `klai-primary` and `klai-fast` accepted. Any other model value returns 400.

### Task 4: Knowledge Management Endpoints

**Files to modify:**
- `klai-portal/backend/app/api/partner.py` (add routes)
- `klai-portal/backend/app/services/partner_knowledge.py` (new service)

**Endpoints:**

`GET /partner/v1/knowledge-bases`
- Returns list of KBs the key has access to (from `allowed_kb_ids`)
- Response: `[{"id": "uuid", "name": "...", "slug": "org", "document_count": 42}]`

`POST /partner/v1/knowledge/documents`
- Requires `permissions.knowledge_write == true`
- Validates target `kb_id` is in key's `allowed_kb_ids`
- Proxies to ingest-api: `POST /ingest/v1/document`
- Request: `{"kb_id": "uuid", "content": "...", "title": "...", "source_type": "partner_api", "content_type": "text/plain"}`
- Response: `{"document_id": "uuid", "chunks_created": 5, "status": "ingested"}`

`DELETE /partner/v1/knowledge/documents/{document_id}`
- Requires `permissions.knowledge_write == true`
- Deletes document chunks from Qdrant by `document_id` filter
- Proxies delete to ingest-api

### Task 5: Feedback Endpoint

**Files to modify:**
- `klai-portal/backend/app/api/partner.py` (add route)

**Endpoint: `POST /partner/v1/feedback`**

Request:
```json
{
  "message_id": "chatcmpl-xxx",
  "rating": "thumbsUp",
  "text": "This answer was accurate and helpful"
}
```

**Flow:**
1. Validate API key, check `permissions.feedback == true`
2. Create `PortalFeedbackEvent` (reuse existing model)
3. Correlate with retrieval log (same time-window logic as LibreChat feedback)
4. Schedule Qdrant quality score update if correlated
5. Emit product event

This reuses the existing feedback pipeline from `klai-portal/backend/app/api/internal.py` (lines 406-504), adapted for partner auth instead of internal secret.

### Task 6: Portal UI — API Key Management

**Files to create/modify:**
- `klai-portal/frontend/src/routes/app/settings/api-keys.tsx` (new page)
- `klai-portal/frontend/src/api/partner-keys.ts` (new API client)
- Navigation update in settings layout

**Portal Endpoints (admin, Zitadel auth):**
- `POST /api/partner-keys` — Create new key (returns full key once)
- `GET /api/partner-keys` — List keys (prefix + name + last_used only)
- `PATCH /api/partner-keys/{id}` — Update name, permissions, KB scope, rate limit
- `DELETE /api/partner-keys/{id}` — Revoke key

**UI Features:**
- Create key dialog: name, select KBs, permissions checkboxes, rate limit
- Key list: name, prefix, last used, active toggle
- Copy key on creation (one-time display with warning)
- KB scope editor: multi-select from org's KBs

### Task 7: Caddy Routing

**Files to modify:**
- `deploy/caddy/Caddyfile` (add route)

Add route for `api.getklai.com/partner/*` → `portal-api:8000`:
```
@partner path /partner/*
handle @partner {
    rate_limit {remote.ip} 120r/m
    reverse_proxy portal-api:8000
}
```

Alternatively, the partner routes can be served on the existing `api.getklai.com` domain since they're just new FastAPI routes in portal-api.

## Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| httpx | existing | Async HTTP client for LiteLLM + retrieval-api streaming |
| Redis | existing | Rate limiting sliding window |
| SQLAlchemy | existing | PartnerAPIKey model |
| Alembic | existing | Database migration |

No new external dependencies required.

## Risk Analysis

| Risk | Impact | Mitigation |
|---|---|---|
| API key leakage | High | SHA-256 hash storage, key shown once, rotation support |
| Prompt injection via ingested content | Medium | Same risk as existing ingest pipeline — no new attack surface |
| Rate limit bypass | Medium | Redis-based sliding window per key + Caddy per-IP limit |
| LLM cost abuse | High | Per-key rate limits, model restricted to klai-primary/klai-fast |
| KB scope escalation | High | Server-side enforcement: every request checks `allowed_kb_ids` |

## Implementation Order

1. **Task 1** — Data model & migration (foundation)
2. **Task 2** — Auth middleware (required by all endpoints)
3. **Task 3** — Chat completions (core value, most complex)
4. **Task 5** — Feedback (reuses existing pipeline)
5. **Task 4** — Knowledge management (lower priority for initial launch)
6. **Task 6** — Portal UI (can be done in parallel with Tasks 3-5)
7. **Task 7** — Caddy routing (deployment config)

## Estimated Scope

- **New files:** ~8 (models, services, routes, tests, frontend)
- **Modified files:** ~5 (model registry, route registry, Caddy, navigation)
- **New DB tables:** 1 (`partner_api_keys`)
- **New Alembic migration:** 1
- **Domains touched:** Backend (portal-api), Frontend (portal UI), Infrastructure (Caddy)
