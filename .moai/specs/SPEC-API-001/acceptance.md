# Acceptance Criteria: SPEC-API-001 — Partner API

## Test Scenarios

### Scenario 1: Partner creates and uses an API key with scoped KB access

**Given** a portal admin is logged in as the org owner of `acme`
**And** the org has two knowledge bases: `kb-docs` and `kb-support`
**When** the admin navigates to Settings → API Keys and creates a new key named "External Chat" with permissions `chat=true, knowledge_read=true, knowledge_write=false, feedback=true`, scoped to `kb-docs` only, with a rate limit of 60 rpm
**Then** the creation response returns the full key in the format `pk_live_<40-hex-chars>` exactly once
**And** a new row is inserted in `partner_api_keys` with `key_hash` set to the SHA-256 of the full key
**And** `allowed_kb_ids` contains only the UUID of `kb-docs`
**And** subsequent list calls return the key's prefix but never the full key

### Scenario 2: Chat completion with KB-scoped RAG and streaming

**Given** a partner API key `pk_live_abc...` exists with access to `kb-docs` and `chat=true`
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
**And** the streamed content mentions "14 days"
**And** the retrieval log contains an entry with the chunk IDs that contributed to the answer, tagged with `partner_key_id`
**And** the LiteLLM call was made with model `klai-primary` (verified in LiteLLM audit log)

### Scenario 3: Chat completion rejects non-EU model and out-of-scope KB

**Given** a partner API key exists with access only to `kb-docs`
**When** the partner sends a chat completions request with `"model": "gpt-4"`
**Then** the response status is 400 with error type `invalid_request_error` and message listing `klai-primary` and `klai-fast` as allowed models

**When** the partner sends a chat completions request with `"knowledge_bases": ["<uuid-of-kb-support>"]` (a KB not in the key's scope)
**Then** the response status is 403 with error type `permission_error`
**And** the error message does NOT reveal whether `kb-support` exists in the org

### Scenario 4: Knowledge document ingestion and deletion

**Given** a partner API key with `knowledge_write=true` and access to `kb-docs`
**When** the partner sends `POST /partner/v1/knowledge/documents` with body
```json
{
  "kb_id": "<uuid-of-kb-docs>",
  "title": "Shipping FAQ",
  "content": "We ship within 2 business days to all EU countries.",
  "content_type": "text/plain"
}
```
**Then** the response status is 201 with body `{"document_id": "<uuid>", "chunks_created": 1, "status": "ingested"}`
**And** the document is retrievable via a subsequent chat completion query about shipping

**When** the partner sends `DELETE /partner/v1/knowledge/documents/<document_id>`
**Then** the response status is 204
**And** a subsequent chat completion query about shipping no longer returns the deleted content

**When** a partner key without `knowledge_write` attempts the same `POST`
**Then** the response status is 403

### Scenario 5: Feedback correlates with retrieval log and triggers quality boost

**Given** a partner chat completion was generated with `message_id = chatcmpl-xyz` at time T, using chunks `[chunk-1, chunk-2, chunk-3]`
**And** the retrieval log has recorded those chunks against `message_id = chatcmpl-xyz`
**When** the partner sends `POST /partner/v1/feedback` within 2 minutes with body
```json
{
  "message_id": "chatcmpl-xyz",
  "rating": "thumbsUp",
  "text": "Exactly what I needed"
}
```
**Then** the response status is 201
**And** a new row is inserted in `portal_feedback_events` with `rating=thumbsUp`, `source=partner_api`, and no `user_id`
**And** the event is correlated with the retrieval log, and a Qdrant quality score update is scheduled for the three chunks

**When** the same feedback request is sent a second time
**Then** the response status is 200 and no duplicate row is created

### Scenario 6: Rate limit enforcement

**Given** a partner API key with `rate_limit_rpm=60`
**When** the partner sends 60 requests to `/partner/v1/chat/completions` within 30 seconds
**Then** all 60 requests receive a 200 response

**When** the partner sends a 61st request within the same minute
**Then** the response status is 429 with header `Retry-After: <seconds>` and body `{"error": {"type": "rate_limit_error"}}`

**When** the partner waits until the sliding window clears
**Then** subsequent requests succeed again with 200

### Scenario 7: Revoked key is rejected

**Given** a partner API key `pk_live_abc...` is active
**When** the portal admin revokes the key via the portal UI
**Then** the `active` field is set to `false` in `partner_api_keys`

**When** the partner sends any request with the revoked key
**Then** the response status is 401 and the key is NOT updated with a new `last_used_at`

---

## Edge Cases

| Case | Expected Behavior |
|---|---|
| Request without `Authorization` header | 401 `authentication_error` |
| Request with malformed key (not `pk_live_` prefix) | 401 `authentication_error` |
| Request with `Authorization: Bearer <jwt>` (wrong auth type) | 401 `authentication_error` |
| Chat request with empty `messages` array | 400 `invalid_request_error` |
| Chat request with only `system` messages (no user turn) | 400 `invalid_request_error` |
| Ingest document > 10 MB | 413 `payload_too_large` |
| Ingest to KB that was removed from key's scope after key creation | 403 `permission_error` |
| Feedback with unknown `message_id` | 201 created but NOT correlated, no quality boost |
| Concurrent requests at rate limit boundary | Redis sliding window handles atomically, no over-allow |
| LiteLLM timeout (> 60s) | 504 `timeout_error` with request_id logged |
| Retrieval-api returns zero chunks | Chat completion proceeds with empty context, logged as `retrieval_empty` |

---

## Quality Gates

- **Test coverage:** >= 85% for new code (`klai-portal/backend/app/api/partner.py`, `services/partner_chat.py`, `services/partner_knowledge.py`, `api/partner_dependencies.py`, models)
- **LSP errors:** 0 (enforced by lsp_quality_gates in config.yaml)
- **Type errors:** 0 (mypy strict on new modules)
- **Lint errors:** 0 (ruff on new modules)
- **Security:** No plaintext keys in logs, no PII in error messages, all secrets via environment
- **Performance:** Chat completions p95 TTFT < 1500 ms measured in integration tests with mocked retrieval
- **Integration tests:** All 7 scenarios pass end-to-end with mocked retrieval-api and LiteLLM
- **Alembic migration:** Forward and backward migration both tested; `partner_api_keys` table is idempotently created and dropped
