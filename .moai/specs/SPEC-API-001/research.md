# Research: SPEC-API-001 — Partner API

## 0. Plan Phase Decisions (v0.2.0)

Four decisions were made during Phase 1 planning after manager-strategy reviewed the original draft against the real codebase:

1. **KB scope uses portal integer IDs via junction table**, not slugs or UUIDs. Reason: KBs use integer primary keys in `portal_knowledge_bases`; slugs are only unique within an org and not stable across rename/delete/recreate. UUIDs don't exist for KBs at all. Junction table allows per-KB access levels.

2. **Append-only semantics — no delete operations via Partner API.** Reason: destructive actions belong to authenticated portal admin UI, not programmatic API keys. No `DELETE` endpoint is exposed for documents, KBs, or connectors. This also avoids the problem that `klai-knowledge-ingest` has no per-document delete endpoint today.

3. **Knowledge append goes directly to the knowledge layer**, equivalent to `save_org_knowledge()` in the MCP. It does NOT go via Klai Docs / Gitea. Reason: partners want kennis toegevoegd, not markdown pages on a docs site.

4. **Feedback correlation uses the existing 60s-before / 10s-after window**, not a new 2-minute window. Reason: consistency with LibreChat feedback handler in `find_correlated_log`. No concrete reason a partner API would need a different window.

5. **Admin layer in scope now** — new `/admin/integrations` section in the portal, accessible to admins and owners. Reason: forcing the admin flow early forces the backend to be well-designed, avoids the "how do we create keys in production" gap, and keeps the feature complete for launch.

## 1. Architecture Analysis

### Current Knowledge Layer Architecture

The Klai knowledge layer consists of three internal services that are not directly exposed to external consumers:

**klai-knowledge-ingest** (document ingestion):
- Endpoint: `POST /ingest/v1/document`
- Accepts: `IngestRequest` with `org_id`, `kb_slug`, `content`, optional `user_id`, `source_type`, `content_type`
- Chunking: 1500-char chunks with 200-char overlap (configurable via `skip_chunking`)
- Embedding: Dense (TEI, BAAI/bge-m3) + sparse (bge-m3-sparse) on gpu-01
- Auth: `X-Internal-Secret` bearer token only
- File: `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`

**klai-retrieval-api** (knowledge retrieval):
- Endpoint: `POST /retrieve/v1/search` (assumed from code patterns)
- Pipeline: coreference resolution → hybrid embedding → gate check → parallel search (Qdrant + Graphiti) → RRF merge → link expansion → reranking → quality boost → top-10
- Auth: `X-Internal-Secret` bearer token only
- Key files:
  - `klai-retrieval-api/retrieval_api/api/retrieve.py` — main endpoint
  - `klai-retrieval-api/retrieval_api/services/search.py` — Qdrant/BM25 hybrid
  - `klai-retrieval-api/retrieval_api/services/reranker.py` — Infinity reranker
  - `klai-retrieval-api/retrieval_api/quality_boost.py` — feedback-based scoring

**klai-knowledge-mcp** (MCP tools for LibreChat):
- FastMCP server exposing: `save_personal_knowledge()`, `save_org_knowledge()`, `save_to_docs()`
- Connected to LibreChat via streamable-http at `http://klai-knowledge-mcp:8080/mcp`
- Auth: `X-User-ID`, `X-Org-ID`, `X-Internal-Secret` headers
- File: `klai-knowledge-mcp/main.py`

### Knowledge Base Data Model

Knowledge is organized as **Knowledge Bases (KBs)** per organization:

- Model: `PortalKnowledgeBase` in `klai-portal/backend/app/models/knowledge_bases.py`
- Fields: `id`, `org_id`, `slug` (e.g., "org", "personal", "group:{id}"), `visibility` (internal/public)
- Access control: `PortalUserKBAccess` and `PortalGroupKBAccess` junction tables with grants
- RLS-protected with `org_id` scoping

**Important:** There is no "helpcenter" concept yet. KBs with slug "org" serve as the org-wide knowledge base. External partners would need access scoped to specific KB IDs.

### Current Auth Architecture

- **User auth:** Zitadel OIDC (session-based, not suitable for server-to-server)
- **Inter-service auth:** `X-Internal-Secret` shared bearer token
- **API key infrastructure:** Portal supports API key generation (`klai-portal/backend/app/api/dependencies.py`) but not yet used for external access
- **Security module:** AES-256-GCM encryption for secrets in `klai-portal/backend/app/core/security.py`

### Feedback System

- LibreChat patch sends feedback to `POST /internal/v1/kb-feedback` (fire-and-forget)
- Model: `PortalFeedbackEvent` — org_id, conversation_id, message_id, rating, tag, feedback_text, chunk_ids
- Privacy: NO user_id column (SPEC-KB-015)
- Correlation: matches retrieval logs within 2-minute time window
- Quality boost: chunks with `feedback_count >= 3` get score increase during retrieval
- Key files:
  - `klai-portal/backend/app/models/feedback_events.py`
  - `klai-portal/backend/app/api/internal.py` (lines 406-504)
  - `klai-portal/backend/app/services/quality_scorer.py`

### LLM Routing

- All LLM calls go through LiteLLM proxy at `http://litellm:4000/v1`
- Model aliases: `klai-fast`, `klai-primary`, `klai-large`
- LiteLLM has a knowledge hook (`deploy/litellm/klai_knowledge.py`) that intercepts chat completions and injects retrieval context
- Per-tenant rate limiting via LiteLLM team keys

## 2. Existing Patterns & Conventions

### Internal API Pattern
All internal APIs follow the same pattern:
- `X-Internal-Secret` bearer auth
- `X-Org-ID` header for tenant context
- FastAPI router with Pydantic request/response models
- RLS enforcement via `set_tenant()` middleware

### LibreChat Integration Pattern
LibreChat connects to Klai services via:
1. LiteLLM proxy (OpenAI-compatible) for chat completions
2. MCP servers (streamable-http) for tool access
3. Portal internal API for feedback

### Provisioning Pattern
Per-tenant provisioning creates: Docker container + MongoDB + OIDC app + LiteLLM team + Caddy route.

## 3. Reference Implementations

### OpenAI Chat Completions API (industry standard)
The Partner API should follow the OpenAI chat completions format:
- `POST /v1/chat/completions` with `model`, `messages[]`, `stream`, `temperature`
- SSE streaming with `data: {"choices": [{"delta": {"content": "..."}}]}`
- This is already what LiteLLM speaks internally

### LiteLLM Knowledge Hook (internal reference)
`deploy/litellm/klai_knowledge.py` shows how retrieval is injected into chat completions:
1. Intercept user message
2. Call retrieval-api with org context
3. Inject retrieved chunks as system message prefix
4. Forward to LLM

The Partner API chat endpoint would follow a similar pattern but with KB-scoped retrieval.

### Existing API Key Infrastructure
`klai-portal/backend/app/api/dependencies.py` has bearer token validation. The Partner API key system would extend this pattern with:
- Hashed key storage (SHA-256)
- Key → org + KB-scope resolution
- Rate limiting metadata

## 4. Risks & Constraints

### Security Risks
- **API key leakage:** Keys must be hashed (SHA-256), never stored plaintext. Rotation mechanism needed.
- **KB scope enforcement:** Every request must verify the key's KB permissions before calling retrieval/ingest.
- **Rate limiting:** Per-key rate limits to prevent abuse. Caddy already does 120/min per IP.
- **Prompt injection:** External content ingested via Partner API could contain prompt injection. Same risk as current ingest pipeline.

### Technical Constraints
- **EU model policy:** All LLM calls must go through LiteLLM with `klai-primary`/`klai-fast` aliases only.
- **No direct service exposure:** Partner API must proxy through portal-api, never expose ingest/retrieval directly.
- **Streaming:** Chat completions must support SSE streaming for real-time responses.
- **Feedback correlation:** Partner API feedback must integrate with existing quality boost pipeline (retrieval log correlation).

### Architectural Constraints
- **Multi-tenancy:** Partner keys are scoped to a single org. No cross-org access.
- **KB granularity:** Keys can access specific KBs within an org, not all KBs by default.
- **No user context:** Partner API operates at org level, not user level (no personal KBs).

## 5. Recommendations

### Phased Implementation
1. **Phase 1:** API key management + chat completions (core value)
2. **Phase 2:** Knowledge management (document CRUD)
3. **Phase 3:** Feedback integration + analytics

### API Design
- Base path: `/partner/v1/` to separate from internal APIs
- OpenAI-compatible chat completions format
- Standard REST for knowledge management
- API key via `Authorization: Bearer pk_...` header

### Data Model
- New `PartnerAPIKey` model with: `id`, `org_id`, `key_hash`, `key_prefix` (for identification), `name`, `permissions` (JSONB), `rate_limit`, `active`, `created_at`, `last_used_at`
- New `PartnerAPIKeyKBAccess` junction table: `key_id`, `kb_id`, `permission` (read/write/both)
- Alembic migration for new tables

### Chat Completions Flow
```
Partner client → POST /partner/v1/chat/completions
  → Validate API key → Resolve org + KB scope
  → Call retrieval-api with scoped KB IDs
  → Build prompt with retrieved context
  → Stream via LiteLLM (klai-primary)
  → Return SSE response
  → Log retrieval for feedback correlation
```
