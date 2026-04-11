---
id: SPEC-API-001
version: 0.2.0
status: draft
created: 2026-04-10
updated: 2026-04-11
author: Mark Vletter
priority: high
---

# SPEC-API-001: Partner API

## HISTORY

### v0.2.0 (2026-04-11)
- KB scope now uses portal knowledge base integer IDs via junction table, not slugs or UUIDs
- Append-only semantics: no DELETE endpoints of any kind (documents, KBs, connectors)
- Knowledge ingestion is direct to the knowledge layer (equivalent to `save_org_knowledge`), never via Klai Docs / Gitea
- Feedback correlation window aligned with existing 60-seconds-before / 10-seconds-after window used by LibreChat feedback
- Admin layer brought into scope: `/admin/integrations` list + detail views, Zitadel-authed admin endpoints, per-KB access level (read / read_write), audit logging
- New `PartnerApiKeyKbAccess` junction table replaces JSONB `allowed_kb_ids` array
- Permission flags renamed to `chat`, `feedback`, `knowledge_append` (no write/delete)

### v0.1.0 (2026-04-10)
- Initial draft

---

## Goal

Enable external parties to integrate their own chat clients with Klai's knowledge layer through a stable, versioned REST API. External clients authenticate with API keys, access a curated subset of knowledge bases with per-KB access levels, submit chat completions (with RAG under the hood), append new knowledge directly to the knowledge layer, and provide feedback on answers. Portal admins and owners manage integrations (create, scope, revoke) through a new Integrations section in the admin portal.

## Success Criteria

- External parties can authenticate with a partner API key and receive streaming chat completions that search only their allowed knowledge bases
- API keys scope access to specific knowledge bases with per-KB access levels (read or read_write)
- Chat completions follow the OpenAI chat completions format (request and response, including SSE streaming)
- All LLM calls route through LiteLLM using `klai-primary` or `klai-fast` aliases — no other models accepted
- Feedback from partner clients integrates with the existing quality boost pipeline using the same correlation window as LibreChat feedback
- Internal services (klai-knowledge-ingest, klai-retrieval-api) are never exposed directly; the Partner API is the only external surface
- Portal admins and org owners can create, scope, revoke, and monitor integrations through `/admin/integrations` in the portal UI
- No destructive operations possible through the Partner API (no delete of documents, KBs, or connectors)
- Knowledge appended via Partner API lands directly in the knowledge layer (Qdrant + Graphiti), never via the docs pipeline
- Per-key rate limits prevent abuse while allowing normal partner usage

---

## Environment

- **Portal backend:** Python 3.12, FastAPI, SQLAlchemy (async), Alembic, PostgreSQL, uv
- **Portal frontend:** React 19, Vite, TypeScript 5.9, TanStack Router, TanStack Query, Mantine 8, Paraglide i18n, Tailwind 4
- **Inter-service HTTP:** httpx (async streaming), existing retrieval-api and knowledge-ingest endpoints
- **LLM proxy:** LiteLLM at `http://litellm:4000/v1` with `klai-primary` / `klai-fast` aliases
- **Rate limiting:** Redis sliding-window counters
- **Key storage:** SHA-256 hashed keys with `pk_live_` prefix in `partner_api_keys` table
- **KB access:** junction table `partner_api_key_kb_access` mapping keys to `portal_knowledge_bases.id` with `access_level` enum
- **Reverse proxy:** Caddy routes `/partner/v1/*` on `api.getklai.com` to `portal-api:8010`
- **Auth (external):** API key Bearer token for partner endpoints
- **Auth (admin):** Zitadel OIDC session with `admin` or `owner` role for `/api/integrations` endpoints

## Assumptions

- The existing retrieval-api accepts Zitadel string `org_id` and a list of `kb_slugs` to scope search (portal-api translates int `kb_id` → slug before calling)
- The existing knowledge-ingest accepts `org_id`, `kb_slug`, and document content via `POST /ingest/v1/document` (direct path to knowledge layer, not via docs)
- The existing quality boost pipeline (SPEC-KB-015) can correlate feedback with retrieval logs regardless of feedback origin
- Partners use their own user/session management; the Partner API has no concept of end users
- Partners send the complete conversation history on every chat completions request (stateless)
- LiteLLM supports streaming SSE responses for the configured model aliases
- Existing `PortalKnowledgeBase` model uses integer primary keys, with `UNIQUE(org_id, slug)` for identification
- The existing `find_correlated_log` function in `app/services/retrieval_log.py` uses a 60-seconds-before / 10-seconds-after window and can be reused as-is

---

## Out of Scope

- Tool calling, function calling, vision, or audio endpoints — chat completions text only
- Per-end-user authentication within the partner's scope — partners operate at org level only
- Personal knowledge bases — partners cannot access `slug:personal` KBs
- Custom system prompts per partner — system prompts are constructed by the Partner API from retrieved context
- **Delete of any kind through the Partner API** — no delete of documents, knowledge bases, or connectors. Destructive actions are only possible through the authenticated portal admin UI, not via programmatic API keys.
- **Docs-route ingestion** — knowledge added via the Partner API goes directly to the knowledge layer (Qdrant + Graphiti), equivalent to the `save_org_knowledge` MCP tool. It never passes through Klai Docs or Gitea.
- **Knowledge base management** — partners cannot create, rename, or delete KBs. Those rights remain with portal admins.
- **Connector management** — partners cannot add, configure, or remove connectors (webcrawlers, GitHub integrations, etc.)
- Billing or usage metering beyond rate limiting — cost tracking is handled by LiteLLM team keys
- OAuth 2.0 client credentials flow — API keys only for v1
- Webhooks for async ingest jobs — synchronous ingest only
- IP allowlisting for partner keys — deferred to v2
- Key expiry dates — deferred to v2
- Detailed usage log UI in the admin portal — v1 links out to Grafana/VictoriaLogs with a pre-scoped query

---

## Requirements

### REQ-1: Partner API Key Model and Lifecycle

The system SHALL provide a `PartnerAPIKey` model scoped to an organization with SHA-256 hashed key storage and per-KB access levels via a junction table.

**REQ-1.1:** WHEN a portal admin or owner creates a partner API key, the system SHALL generate a key in the format `pk_live_` followed by 40 hexadecimal characters, store only the SHA-256 hash, and return the full key exactly once in the creation response.

**REQ-1.2:** The `partner_api_keys` table SHALL contain the fields `id` (UUID PK), `org_id` (Integer FK), `name` (String), `description` (String, optional), `key_prefix` (String, first 12 characters of the full key for display), `key_hash` (String, SHA-256 hex), `permissions` (JSONB with boolean keys `chat`, `feedback`, `knowledge_append`), `rate_limit_rpm` (Integer), `active` (Boolean), `last_used_at` (DateTime, nullable), `created_at` (DateTime), `created_by` (UUID, Zitadel user id of creating admin).

**REQ-1.3:** The `partner_api_key_kb_access` junction table SHALL contain `partner_api_key_id` (UUID FK), `kb_id` (Integer FK to `portal_knowledge_bases.id`), and `access_level` (String enum: `read` or `read_write`). A primary key constraint SHALL exist on `(partner_api_key_id, kb_id)`.

**REQ-1.4:** WHEN a portal admin or owner revokes a partner key, the system SHALL set `active=false` and the key SHALL be rejected on subsequent authentication attempts with HTTP 401. Revocation SHALL NOT be reversible (admins must create a new key instead).

**REQ-1.5:** Both `partner_api_keys` and `partner_api_key_kb_access` tables SHALL be RLS-protected with the same `org_id` policy pattern as other tenant-scoped portal tables.

**REQ-1.6:** WHEN the system validates an incoming partner key, it SHALL update `last_used_at` asynchronously without blocking the request (using `asyncio.create_task`).

**REQ-1.7:** By default, newly created keys SHALL have `knowledge_append` set to `false`. Admins must explicitly enable this permission during creation or later via edit.

### REQ-2: Partner API Authentication and Authorization

The system SHALL authenticate all `/partner/v1/*` requests using the partner API key and enforce permission, KB-scope, and rate-limit checks before routing to downstream services.

**REQ-2.1:** WHEN a request arrives at any `/partner/v1/*` endpoint, the system SHALL extract the `Authorization: Bearer pk_...` header, compute the SHA-256 hash, and look it up in `partner_api_keys`.

**REQ-2.2:** IF the key is missing, malformed, not found, or inactive, THEN the system SHALL return HTTP 401 with an error body `{"error": {"type": "authentication_error", "message": "..."}}`. The error message SHALL NOT distinguish between "not found" and "inactive" to prevent enumeration.

**REQ-2.3:** WHEN a valid key is resolved, the system SHALL verify that the endpoint's required permission bit (`chat`, `feedback`, or `knowledge_append`) is enabled for the key. IF the permission is not granted, THEN the system SHALL return HTTP 403.

**REQ-2.4:** WHILE a partner key is active, the system SHALL enforce a Redis-based sliding-window rate limit of `rate_limit_rpm` requests per minute per key. IF the limit is exceeded, THEN the system SHALL return HTTP 429 with a `Retry-After` header in seconds.

**REQ-2.5:** WHEN a chat completions or knowledge request references specific knowledge base IDs, the system SHALL verify that every requested KB id is present in the key's `partner_api_key_kb_access` entries AND has the required `access_level`:
- `chat` and `knowledge-bases` endpoints require `read` or `read_write`
- `knowledge` append endpoint requires `read_write`

IF any requested KB is not allowed, THEN the system SHALL return HTTP 403 without revealing whether the KB exists in the organization.

**REQ-2.6:** The system SHALL NEVER log the plaintext API key, its SHA-256 hash, or partner request bodies containing secrets. Structured logs SHALL bind `partner_key_id`, `org_id`, `endpoint`, `status_code`, and `duration_ms` context only.

### REQ-3: OpenAI-Compatible Chat Completions with RAG

The system SHALL expose `POST /partner/v1/chat/completions` accepting an OpenAI-compatible request and returning a streaming SSE response produced by LiteLLM over retrieved context.

**REQ-3.1:** The chat completions request body SHALL accept `messages` (array of role+content objects), `model` (string), `stream` (boolean, default true), `temperature` (number, default 0.7), and an optional `knowledge_base_ids` (array of portal KB integer IDs to override the key's default scope — must be a subset of the key's allowed KBs).

**REQ-3.2:** The system SHALL accept only `klai-primary` and `klai-fast` as model values. IF any other model value is provided, THEN the system SHALL return HTTP 400 with an error body listing the allowed models.

**REQ-3.3:** WHEN a chat completions request arrives, the system SHALL extract the last user message, resolve portal `kb_id` values to `kb_slug` values via the `portal_knowledge_bases` table (scoped to the key's org), resolve the portal `org_id` to the Zitadel string org id, call the retrieval-api with this translated scope, and build a system prompt that includes the retrieved context chunks before forwarding to LiteLLM.

**REQ-3.4:** WHEN `stream=true`, the system SHALL proxy the LiteLLM SSE response to the partner client in OpenAI-compatible format (byte-for-byte passthrough with connection lifecycle handling). WHEN `stream=false`, the system SHALL collect the full LiteLLM response and return a single JSON body.

**REQ-3.5:** WHEN a chat completion is generated, the system SHALL asynchronously log the retrieval context (chunk IDs, scores, query, message_id) to the retrieval log via `asyncio.create_task`, so that subsequent feedback can correlate with the answer using the same mechanism as LibreChat feedback.

**REQ-3.6:** IF the retrieval-api or LiteLLM calls fail, THEN the system SHALL return HTTP 502 with an error body and log the failure with `partner_key_id`, `org_id`, and `request_id` for debugging.

### REQ-4: Knowledge Append and Listing

The system SHALL expose read-only listing and append-only ingestion endpoints for knowledge, with no delete operations.

**REQ-4.1:** The endpoint `GET /partner/v1/knowledge-bases` SHALL return the list of KBs the key has access to (via `partner_api_key_kb_access`), each with `id`, `name`, `slug`, and `access_level`. It requires either the `chat` or `knowledge_append` permission.

**REQ-4.2:** The endpoint `POST /partner/v1/knowledge` SHALL accept `kb_id` (integer), `title` (string, optional), `content` (string), optional `source_type` (default `partner_api`), and optional `content_type` (default `text/plain`). The system SHALL verify `kb_id` has `read_write` access_level for this key AND `permissions.knowledge_append` is true, then proxy the request to knowledge-ingest `POST /ingest/v1/document` with the appropriate `kb_slug`. This path goes directly to the knowledge layer — never via Klai Docs or Gitea.

**REQ-4.3:** WHERE knowledge ingestion succeeds, the response SHALL include `knowledge_id`, `chunks_created`, and `status`.

**REQ-4.4:** IF a knowledge payload exceeds 10 MB of raw content, THEN the system SHALL return HTTP 413 with an error body indicating the size limit.

**REQ-4.5:** The Partner API SHALL NOT expose any delete endpoints. No endpoint SHALL delete documents, knowledge bases, or connectors through the partner surface. Destructive operations are only possible through authenticated portal admin UI actions.

### REQ-5: Feedback Integration

The system SHALL expose `POST /partner/v1/feedback` that creates feedback events integrated with the existing quality boost pipeline.

**REQ-5.1:** The feedback endpoint SHALL accept `message_id` (string), `rating` (`thumbsUp` or `thumbsDown`), optional `text` (string), and optional `tag` (string). WHEN a request arrives, the system SHALL verify `permissions.feedback` is true.

**REQ-5.2:** WHEN a feedback event is created, the system SHALL insert a `PortalFeedbackEvent` row without a `user_id` (following SPEC-KB-015 privacy rules) with `source=partner_api` metadata.

**REQ-5.3:** WHEN a feedback event is inserted, the system SHALL correlate it with retrieval logs using the existing `find_correlated_log` function in `app/services/retrieval_log.py`, which uses a 60-seconds-before / 10-seconds-after window. IF correlated, the system SHALL schedule a Qdrant quality score update for the matched chunks.

**REQ-5.4:** WHERE a feedback event is idempotent (same `message_id` seen twice), the system SHALL return HTTP 200 without creating a duplicate row (idempotency key stored in Redis).

### REQ-6: Admin Integrations Management

The system SHALL provide admin endpoints and a portal UI for managing partner API keys as "Integrations", accessible to users with `admin` or `owner` role.

**REQ-6.1:** WHEN a user accesses `/admin/integrations` endpoints, the system SHALL verify the user has `admin` or `owner` role in the current org via the existing Zitadel role check. IF the user lacks the role, THEN the system SHALL return HTTP 403.

**REQ-6.2:** The endpoint `POST /api/integrations` SHALL accept `name`, optional `description`, `permissions` (object with `chat`, `feedback`, `knowledge_append` booleans), `kb_access` (array of `{kb_id, access_level}` objects), and `rate_limit_rpm` (integer). It SHALL generate a key per REQ-1.1, create the row and junction entries, and return the full key plaintext exactly once in the response body plus the key metadata. The system SHALL validate that every listed `kb_id` belongs to the caller's org.

**REQ-6.3:** The endpoint `GET /api/integrations` SHALL return a list of integrations for the caller's org with `id`, `name`, `description`, `key_prefix`, `active`, `kb_access_count`, `rate_limit_rpm`, `last_used_at`, `created_at`. The full key plaintext SHALL never appear in list responses.

**REQ-6.4:** The endpoint `GET /api/integrations/{id}` SHALL return the full metadata for a single integration including the per-KB access list with KB names and slugs. The full key plaintext SHALL never appear.

**REQ-6.5:** The endpoint `PATCH /api/integrations/{id}` SHALL accept partial updates to `name`, `description`, `permissions`, `kb_access`, `rate_limit_rpm`. The system SHALL replace the `kb_access` rows atomically within a single transaction when that field is provided.

**REQ-6.6:** The endpoint `POST /api/integrations/{id}/revoke` SHALL set `active=false` (irreversible per REQ-1.4) and return the updated metadata.

**REQ-6.7:** The system SHALL emit a product event for every integration lifecycle action (`integration.created`, `integration.updated`, `integration.revoked`) with `org_id`, `actor_user_id`, `integration_id`, and `name`.

**REQ-6.8:** The portal frontend SHALL provide `/admin/integrations` routes with:
- A list view showing all integrations with name, key prefix, status, KB access count, last used, and action menu
- A create flow with sections for basics (name, description), permissions (chat, feedback, knowledge append), KB access (per-KB radio select for none/read/read_write), and rate limit
- A one-time key display modal after creation with copy button and explicit warning that the key is shown only once
- A detail view for editing metadata, permissions, KB access, rate limit, and triggering revocation
- An empty state inviting admins to create the first integration
- A "View logs" link on the detail view that deeplinks to Grafana with a pre-scoped `partner_key_id:<id>` query

**REQ-6.9:** All UI strings SHALL be available in NL and EN via Paraglide i18n, following the existing portal frontend conventions in `klai-portal/frontend/src/routes/admin/`.

---

## Non-Functional Requirements

- **Performance:** Chat completions p95 time-to-first-token SHALL be under 1500 ms including retrieval
- **Privacy:** The Partner API SHALL never log plaintext API keys, SHA-256 hashes, message content, or retrieved chunk content. Admin integration endpoints SHALL also never log plaintext keys except in the immediate create-response body.
- **Observability:** All partner requests SHALL emit structured logs with `request_id`, `partner_key_id`, `org_id`, `endpoint`, `status_code`, and `duration_ms`. Admin actions SHALL emit product events per REQ-6.7.
- **Security:** Partner API keys SHALL be stored hashed only; plaintext keys SHALL only exist in memory during the single creation response. Key comparison SHALL use `hmac.compare_digest` for constant-time verification.
- **EU compliance:** No US cloud provider model aliases or identifiers SHALL appear anywhere in request or response payloads.
- **No destructive operations via API:** The Partner API surface SHALL contain no endpoint capable of deleting or leaking state beyond the authenticated key's own append operations.
