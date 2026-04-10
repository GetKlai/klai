---
id: SPEC-API-001
version: 0.1.0
status: draft
created: 2026-04-10
updated: 2026-04-10
author: Mark Vletter
priority: high
---

# SPEC-API-001: Partner API

## HISTORY

### v0.1.0 (2026-04-10)
- Initial draft
- Scope: OpenAI-compatible chat completions, knowledge management, feedback, API key auth
- KB-scoped partner access as alternative to LibreChat integration

---

## Goal

Enable external parties to integrate their own chat clients with Klai's knowledge layer through a stable, versioned REST API. Partners authenticate with API keys, access a curated subset of knowledge bases, submit chat completions (with RAG under the hood), add or remove knowledge documents, and provide feedback on answers — all without depending on LibreChat and without exposing internal services directly.

## Success Criteria

- External parties can authenticate with a partner API key and receive streaming chat completions that search only their allowed knowledge bases
- API keys scope access to specific knowledge bases with read and write permissions
- Chat completions follow the OpenAI chat completions format (request and response)
- All LLM calls route through LiteLLM using `klai-primary` or `klai-fast` aliases — no other models accepted
- Feedback from partner clients integrates with the existing quality boost pipeline
- Internal services (klai-knowledge-ingest, klai-retrieval-api) are never exposed directly; the Partner API is the only external surface
- Portal admins can create, scope, revoke, and monitor partner keys through the portal UI
- Per-key rate limits prevent abuse while allowing normal partner usage

---

## Environment

- **Portal backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
- **Inter-service HTTP:** httpx (async streaming), existing retrieval-api and ingest-api endpoints
- **LLM proxy:** LiteLLM at `http://litellm:4000/v1` with `klai-primary` / `klai-fast` aliases
- **Rate limiting:** Redis sliding window counters
- **Key storage:** SHA-256 hashed keys with `pk_live_` prefix in `partner_api_keys` table
- **Frontend:** React 19, Vite, TanStack Router, TanStack Query, Mantine 8 (portal UI)
- **Reverse proxy:** Caddy routes `/partner/v1/*` on `api.getklai.com` to portal-api

## Assumptions

- The existing retrieval-api accepts org_id and a list of KB IDs to scope search
- The existing ingest-api accepts org_id, kb_slug, and document content for ingestion
- The existing quality boost pipeline (SPEC-KB-015) can correlate feedback with retrieval logs regardless of the origin of the feedback
- Partners use their own user/session management; the Partner API has no concept of end users
- Partners send the complete conversation history on every chat completions request (stateless)
- LiteLLM supports streaming SSE responses for the configured model aliases
- Existing `PortalKnowledgeBase` model and its RLS policies are sufficient for KB-scoped access

---

## Out of Scope

- Tool calling, function calling, vision, or audio endpoints — chat completions text only
- Per-end-user authentication within the partner's scope — partners operate at org level only
- Personal knowledge bases — partners cannot access `slug:personal` KBs
- Custom system prompts per partner — system prompts are constructed by the Partner API from retrieved context
- Billing or usage metering beyond rate limiting — cost tracking is handled by LiteLLM team keys
- OAuth 2.0 client credentials flow — API keys only for v1
- Webhooks for async ingest jobs — synchronous ingest only

---

## Requirements

### REQ-1: Partner API Key Model and Lifecycle

The system SHALL provide a `PartnerAPIKey` model scoped to an organization with SHA-256 hashed key storage.

**REQ-1.1:** WHEN a portal admin creates a partner API key, the system SHALL generate a key in the format `pk_live_` followed by 40 hexadecimal characters, store only the SHA-256 hash, and return the full key exactly once in the creation response.

**REQ-1.2:** The `PartnerAPIKey` table SHALL contain the fields `id`, `org_id`, `name`, `key_prefix` (first 12 characters of the full key for display), `key_hash` (SHA-256), `permissions` (JSONB with keys `chat`, `knowledge_read`, `knowledge_write`, `feedback`), `rate_limit_rpm`, `allowed_kb_ids` (array of KB UUIDs), `active`, `last_used_at`, `created_at`, `created_by`.

**REQ-1.3:** WHEN a portal admin revokes a partner key, the system SHALL set `active=false` and the key SHALL be rejected on subsequent requests with HTTP 403.

**REQ-1.4:** The `partner_api_keys` table SHALL be RLS-protected with the same `org_id` policy as other tenant-scoped portal tables.

**REQ-1.5:** WHEN the system validates an incoming partner key, it SHALL update `last_used_at` asynchronously without blocking the request.

### REQ-2: Partner API Authentication and Authorization

The system SHALL authenticate all `/partner/v1/*` requests using the partner API key and enforce permission and rate limit checks before routing to downstream services.

**REQ-2.1:** WHEN a request arrives at any `/partner/v1/*` endpoint, the system SHALL extract the `Authorization: Bearer pk_...` header, compute the SHA-256 hash, and look it up in `partner_api_keys`.

**REQ-2.2:** IF the key is missing, malformed, not found, or inactive, THEN the system SHALL return HTTP 401 with an error body `{"error": {"type": "authentication_error", "message": "..."}}`.

**REQ-2.3:** WHEN a valid key is resolved, the system SHALL verify that the endpoint's required permission bit (`chat`, `knowledge_read`, `knowledge_write`, or `feedback`) is enabled for the key. IF the permission is not granted, THEN the system SHALL return HTTP 403.

**REQ-2.4:** WHILE a partner key is active, the system SHALL enforce a sliding-window rate limit of `rate_limit_rpm` requests per minute per key using Redis. IF the limit is exceeded, THEN the system SHALL return HTTP 429 with a `Retry-After` header.

**REQ-2.5:** WHEN a chat completions or knowledge request references specific knowledge base IDs, the system SHALL verify that every requested KB ID is present in the key's `allowed_kb_ids`. IF any KB ID is not allowed, THEN the system SHALL return HTTP 403 without revealing whether the KB exists.

### REQ-3: OpenAI-Compatible Chat Completions with RAG

The system SHALL expose `POST /partner/v1/chat/completions` accepting an OpenAI-compatible request and returning a streaming SSE response produced by LiteLLM over retrieved context.

**REQ-3.1:** The chat completions request body SHALL accept `messages` (array of role+content objects), `model` (string), `stream` (boolean, default true), `temperature` (number, default 0.7), and an optional `knowledge_bases` (array of KB UUIDs to override the key's default scope).

**REQ-3.2:** The system SHALL accept only `klai-primary` and `klai-fast` as model values. IF any other model value is provided, THEN the system SHALL return HTTP 400 with an error body listing the allowed models.

**REQ-3.3:** WHEN a chat completions request arrives, the system SHALL extract the last user message, call the retrieval-api with the resolved org_id and KB scope, and build a system prompt that includes the retrieved context chunks before forwarding to LiteLLM.

**REQ-3.4:** WHEN `stream=true`, the system SHALL proxy the LiteLLM SSE response to the partner client in OpenAI-compatible format. WHEN `stream=false`, the system SHALL collect the full LiteLLM response and return a single JSON body.

**REQ-3.5:** WHEN a chat completion is generated, the system SHALL asynchronously log the retrieval context (chunk IDs, scores, query) to the retrieval log so that subsequent feedback can correlate with the answer.

**REQ-3.6:** IF the retrieval-api or LiteLLM calls fail, THEN the system SHALL return HTTP 502 with an error body and log the failure with the partner key ID and request ID for debugging.

### REQ-4: Knowledge Management Endpoints

The system SHALL expose knowledge management endpoints that proxy to ingest-api while enforcing KB scope and write permissions.

**REQ-4.1:** The endpoint `GET /partner/v1/knowledge-bases` SHALL return the list of KBs from the key's `allowed_kb_ids`, each with `id`, `name`, `slug`, and `document_count`.

**REQ-4.2:** The endpoint `POST /partner/v1/knowledge/documents` SHALL accept `kb_id`, `title`, `content`, and optional `source_type` (default `partner_api`) and `content_type` (default `text/plain`). The system SHALL verify `kb_id` is in `allowed_kb_ids` and `permissions.knowledge_write` is true, then proxy the request to ingest-api.

**REQ-4.3:** The endpoint `DELETE /partner/v1/knowledge/documents/{document_id}` SHALL verify the document belongs to a KB in `allowed_kb_ids` and `permissions.knowledge_write` is true, then delete the document chunks from the retrieval store.

**REQ-4.4:** WHERE a document ingestion succeeds, the response SHALL include `document_id`, `chunks_created`, and `status`.

**REQ-4.5:** IF a document exceeds 10 MB of raw content, THEN the system SHALL return HTTP 413 with an error body indicating the size limit.

### REQ-5: Feedback Integration

The system SHALL expose `POST /partner/v1/feedback` that creates feedback events integrated with the existing quality boost pipeline.

**REQ-5.1:** The feedback endpoint SHALL accept `message_id`, `rating` (`thumbsUp` or `thumbsDown`), optional `text`, and optional `tag`. WHEN a request arrives, the system SHALL verify `permissions.feedback` is true.

**REQ-5.2:** WHEN a feedback event is created, the system SHALL insert a `PortalFeedbackEvent` row without a `user_id` (following SPEC-KB-015 privacy rules) and tag the source as `partner_api`.

**REQ-5.3:** WHEN a feedback event is inserted, the system SHALL correlate it with retrieval logs by matching `message_id` within a 2-minute time window, and IF correlated, schedule a Qdrant quality score update using the existing pipeline.

**REQ-5.4:** WHERE a feedback event is idempotent (same `message_id` seen twice), the system SHALL return HTTP 200 without creating a duplicate row.

---

## Non-Functional Requirements

- **Performance:** Chat completions p95 time-to-first-token SHALL be under 1500 ms including retrieval
- **Privacy:** The Partner API SHALL never log partner API keys, message content, or retrieved chunk content
- **Observability:** All partner requests SHALL emit structured logs with `request_id`, `partner_key_id`, `org_id`, `endpoint`, `status_code`, and `duration_ms`
- **Security:** Partner API keys SHALL be stored hashed only; plaintext keys SHALL only exist in memory during the single creation response
- **EU compliance:** No US cloud provider model aliases or identifiers SHALL appear anywhere in request or response payloads
