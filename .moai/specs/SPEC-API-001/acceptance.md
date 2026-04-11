# Acceptance Criteria: SPEC-API-001 — Partner API

## Test Scenarios

### Scenario 1: Admin creates an integration with per-KB access levels

**Given** Mark is logged in as admin of the org `acme`
**And** the org has two knowledge bases: `kb-docs` (id=42) and `kb-support` (id=87)
**When** Mark navigates to `/admin/integrations`, clicks "New integration", fills in:
- Name: "External Chat"
- Permissions: `chat=true, feedback=true, knowledge_append=false`
- KB access: `kb-docs → read`, `kb-support → none`
- Rate limit: 60 rpm

and clicks Create
**Then** the response returns the full key in the format `pk_live_<40-hex-chars>` exactly once in a modal with a copy button and a warning that the key will not be shown again
**And** a new row is inserted in `partner_api_keys` with `key_hash` set to the SHA-256 of the full key and `knowledge_append=false`
**And** a single row is inserted in `partner_api_key_kb_access` with `kb_id=42, access_level=read`
**And** `kb_id=87` has no row (not in scope)
**And** subsequent list calls show the key prefix but never the full key
**And** a product event `integration.created` is emitted with `actor_user_id=mark`

### Scenario 2: Chat completion with RAG, streaming, and KB-scoped retrieval

**Given** a partner API key exists with access to `kb-docs` only (read), `chat=true`
**And** `kb-docs` contains a document titled "Refund Policy" with the text "Refunds are processed within 14 days"
**When** the partner sends `POST /partner/v1/chat/completions` with body
```json
{
  "model": "klai-primary",
  "stream": true,
  "messages": [
    {"role": "user", "content": "How long does a refund take?"}
  ]
}
```
with header `Authorization: Bearer pk_live_abc...`
**Then** the response status is 200 with content type `text/event-stream`
**And** the stream begins within 1500 ms (p95)
**And** the retrieval-api was called with `org_id=<zitadel_org_id>` (translated from portal int `org_id`) and `kb_slugs=["kb-docs"]` (translated from integer `kb_id=42`)
**And** the streamed content mentions "14 days"
**And** the retrieval log contains an entry with the chunk IDs that contributed to the answer, associated with the message_id
**And** the LiteLLM call used model `klai-primary` (verified in LiteLLM audit log)

### Scenario 3: Chat completion rejects non-EU model and out-of-scope KB

**Given** a partner API key exists with access only to `kb-docs` (id=42)
**When** the partner sends a chat completions request with `"model": "gpt-4"`
**Then** the response status is 400 with error type `invalid_request_error` and message listing `klai-primary` and `klai-fast` as allowed models

**When** the partner sends a chat completions request with `"knowledge_base_ids": [87]` (an id NOT in the key's scope)
**Then** the response status is 403 with error type `permission_error`
**And** the error message does NOT reveal whether KB id 87 exists in the org (generic "Access denied to one or more requested knowledge bases")

### Scenario 4: Knowledge append to a read_write KB

**Given** a partner API key with `knowledge_append=true`, `kb-docs` access at `read_write`
**When** the partner sends `POST /partner/v1/knowledge` with body
```json
{
  "kb_id": 42,
  "title": "Shipping FAQ",
  "content": "We ship within 2 business days to all EU countries.",
  "content_type": "text/plain"
}
```
**Then** the response status is 201 with body `{"knowledge_id": "<uuid>", "chunks_created": 1, "status": "ingested"}`
**And** knowledge-ingest was called at `POST /ingest/v1/document` with `kb_slug="kb-docs"` (translated from `kb_id=42`) and `org_id=<zitadel_org_id>`
**And** the document is retrievable via a subsequent chat completion query about shipping
**And** the document went directly to the knowledge layer — no docs/Gitea path was used

**When** a partner key with only `read` access to `kb-docs` attempts the same POST
**Then** the response status is 403 with error type `permission_error` and generic message

**When** a partner key with `knowledge_append=false` attempts the same POST
**Then** the response status is 403 with error type `permission_error`

### Scenario 5: Feedback correlates with retrieval log and triggers quality boost

**Given** a partner chat completion was generated with `message_id = chatcmpl-xyz` at time T, using chunks `[chunk-1, chunk-2, chunk-3]`
**And** the retrieval log has recorded those chunks against `message_id = chatcmpl-xyz` at T
**When** the partner sends `POST /partner/v1/feedback` within 60 seconds with body
```json
{
  "message_id": "chatcmpl-xyz",
  "rating": "thumbsUp",
  "text": "Exactly what I needed"
}
```
**Then** the response status is 201
**And** a new row is inserted in `portal_feedback_events` with `rating=thumbsUp`, `source=partner_api`, and NO `user_id`
**And** the existing `find_correlated_log` function (60s before / 10s after window) finds the retrieval log entry
**And** a Qdrant quality score update is scheduled for the three chunks

**When** the same feedback request is sent a second time (idempotency key in Redis)
**Then** the response status is 200 and no duplicate row is created

**When** feedback is submitted 75 seconds after the message (outside the 60s window)
**Then** the response status is 201 but NO correlation occurs and NO quality update is scheduled

### Scenario 6: Rate limit enforcement with Retry-After

**Given** a partner API key with `rate_limit_rpm=60`
**When** the partner sends 60 requests to `/partner/v1/chat/completions` within 30 seconds
**Then** all 60 requests receive a 200 response

**When** the partner sends a 61st request within the same minute
**Then** the response status is 429 with header `Retry-After: <seconds>` and body `{"error": {"type": "rate_limit_error"}}`

**When** the partner waits until the sliding window clears
**Then** subsequent requests succeed again with 200

### Scenario 7: Revoked key is rejected and cannot be un-revoked

**Given** a partner API key `pk_live_abc...` is active
**When** Mark clicks Revoke on the integration detail view and confirms
**Then** the `active` field is set to `false` in `partner_api_keys`
**And** a product event `integration.revoked` is emitted

**When** the partner sends any request with the revoked key
**Then** the response status is 401 with the same generic message used for unknown keys (no enumeration leak)
**And** the `last_used_at` is NOT updated

**When** Mark attempts `PATCH /api/integrations/{id}` with `active=true`
**Then** the request is rejected — the field cannot be re-enabled. Admin must create a new key.

### Scenario 8: Admin edits KB access on an existing integration

**Given** an integration exists with access to `kb-docs` (read) and `kb-support` (none)
**When** Mark navigates to `/admin/integrations/{id}`, changes `kb-support` from "none" to "read_write", saves
**Then** a PATCH request is sent with the updated `kb_access` array
**And** the `partner_api_key_kb_access` rows are replaced atomically within a single transaction
**And** a product event `integration.updated` is emitted
**And** the detail view refreshes showing the new access list

**When** Mark enables the `knowledge_append` permission and saves
**Then** the PATCH includes the new permission
**And** subsequent `POST /partner/v1/knowledge` requests to `kb-support` succeed

### Scenario 9: Non-admin cannot access integrations

**Given** a regular user `alice` (no admin or owner role) in the org
**When** Alice navigates to `/admin/integrations` in the portal UI
**Then** the route is hidden from the nav and a direct URL visit shows a 403 / access denied page

**When** Alice calls `GET /api/integrations` directly
**Then** the response status is 403

---

## Edge Cases

| Case | Expected Behavior |
|---|---|
| Request without `Authorization` header | 401 `authentication_error` |
| Request with malformed key (not `pk_live_` prefix) | 401 `authentication_error` |
| Request with `Authorization: Bearer <jwt>` (wrong auth type) | 401 `authentication_error` |
| Chat request with empty `messages` array | 400 `invalid_request_error` |
| Chat request with only `system` messages (no user turn) | 400 `invalid_request_error` |
| Knowledge append > 10 MB content | 413 `payload_too_large` |
| Knowledge append to KB that was removed from key scope after key creation | 403 `permission_error` |
| Feedback with unknown `message_id` | 201 created but NOT correlated, no quality boost |
| Feedback submitted > 60 seconds after message | 201 created, correlation fails (expected) |
| Concurrent requests at rate limit boundary | Redis sliding window handles atomically, no over-allow |
| LiteLLM timeout (> 60s) | 504 `timeout_error` with request_id logged |
| Retrieval-api returns zero chunks | Chat completion proceeds with empty context, logged as `retrieval_empty` |
| Admin creates integration with `kb_id` belonging to another org | 400 `invalid_request_error` (org mismatch) |
| Admin creates integration with `knowledge_append=true` but no KBs at `read_write` level | 400 `invalid_request_error` (useless config) |
| PATCH with empty `kb_access` array | Allowed — revokes all KB access but keeps the key active |
| PATCH attempting to set `active=true` on revoked key | 400 `invalid_request_error` (revocation is irreversible) |

---

## Quality Gates

- **Test coverage:** >= 85% for new code across both backend and frontend
  - Backend: `app/api/partner.py`, `app/api/partner_dependencies.py`, `app/api/admin_integrations.py`, `app/services/partner_chat.py`, `app/services/partner_knowledge.py`, `app/services/partner_feedback.py`, `app/services/partner_keys.py`, `app/services/partner_rate_limit.py`, `app/services/admin_integrations.py`, models
  - Frontend: `src/routes/admin/integrations/`, `src/api/integrations.ts`
- **LSP errors:** 0 (enforced by `lsp_quality_gates` in config.yaml)
- **Type errors:** 0 (pyright strict on new modules, tsc strict on frontend)
- **Lint errors:** 0 (ruff on backend, biome/eslint on frontend)
- **Security:**
  - No plaintext keys in logs ever (verify with grep-based test)
  - No PII in error messages
  - All secrets via environment variables
  - `hmac.compare_digest` used for key hash comparison
- **Performance:** Chat completions p95 TTFT < 1500 ms measured in integration test with mocked retrieval
- **Integration tests:** All 9 scenarios pass end-to-end with mocked retrieval-api, knowledge-ingest, and LiteLLM
- **Alembic migration:** Upgrade and downgrade tested; `partner_api_keys` and `partner_api_key_kb_access` created/dropped idempotently; RLS policies include `IF NOT EXISTS`
- **i18n:** All user-facing strings in `/admin/integrations` have NL and EN translations via Paraglide
- **No destructive endpoints:** Automated check that no DELETE HTTP method exists under `/partner/v1/*` routes
