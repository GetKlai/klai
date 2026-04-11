# Implementation Plan: SPEC-API-001 — Partner API

## Overview

Build a Partner API in portal-api that lets external parties integrate their own chat clients with Klai's knowledge layer, plus an Integrations admin section in the portal UI for key management. The external API is OpenAI-compatible for chat, direct-append for knowledge, and feedback-integrated with the existing quality boost pipeline. The admin UI lives under `/admin/integrations` and is accessible to admins and org owners.

All 4 plan-phase decisions are locked in (see SPEC v0.2.0 HISTORY):
1. KB scope via **Integer IDs** through a junction table (`partner_api_key_kb_access`), not slugs or UUIDs
2. **Append-only** semantics (no delete, direct to knowledge layer, not via docs)
3. Feedback correlation uses **existing 60s/10s window**
4. Admin UI (`/admin/integrations`) **in scope now**, with per-KB read/read_write

## Known Risks (from Phase 1 analysis)

| # | Risk | Mitigation |
|---|---|---|
| 1 | Alembic has 4+ heads branching off `z2a3b4c5d6e7` — our migration must pick one, merge later | Run `alembic heads` before writing TASK-002 and -009. Pick the most recently merged head. |
| 2 | RLS `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` cannot run as `portal_api` role | Include RLS lines in migration for history; operator runbook note in docstring. Manual execution by `klai` superuser post-deploy. |
| 3 | No existing LLM streaming proxy pattern in portal-api — backpressure/cancellation edge cases | Use byte-for-byte SSE passthrough via `StreamingResponse`. Reference FastAPI patterns. Unit test with mocked async iterator. |
| 4 | Retrieval-api requires Zitadel string `org_id`, portal uses int. Needs lookup every call. | Reuse lookup pattern from `internal.py:383`. Cache per request via FastAPI dependency. |
| 5 | `PortalFeedbackEvent` `KbFeedbackIn` Pydantic schema is LibreChat-specific (tenant/user ids, message_created_at) — not directly reusable. | Create partner-specific input model, translate to DB insert with `source=partner_api` metadata. Share the downstream pipeline (correlation, quality update, event emission). |
| 6 | Portal-api listens on port **8010**, not 8000 | Caddy task uses `portal-api:8010`. |
| 7 | API key plaintext leakage into logs | @MX:WARN on key extract function + structlog context binding helper that strips Authorization header. Code review checklist. |
| 8 | No existing sliding-window Redis rate limiter in portal-api | New utility `partner_rate_limit.py`. Use Redis ZSET with timestamps, ZADD/ZREMRANGEBYSCORE/ZCARD pattern. |

## Task Decomposition (15 atomic TDD tasks)

Each task is a single RED-GREEN-REFACTOR cycle, ~50-200 LOC including tests.

### TASK-001: PartnerAPIKey + PartnerApiKeyKbAccess models
**Files:** `klai-portal/backend/app/models/partner_api_keys.py` (new)
**Requirement:** REQ-1.2, REQ-1.3
**Test:** assert table name, column types, primary keys, nullability, JSONB default, relationships
**Size:** S (~100 impl + 80 test)
**Pattern reference:** `app/models/connectors.py` (UUID PK + JSONB pattern)
**Dependencies:** none

### TASK-002: Alembic migration for partner tables
**Files:** `klai-portal/backend/alembic/versions/<rev>_add_partner_api_keys.py` (new)
**Requirement:** REQ-1.2, REQ-1.3, REQ-1.5
**Pre-task:** `alembic heads` check to pick `down_revision`
**Test:** upgrade → tables exist with expected columns; downgrade → tables removed; RLS policies created idempotently (`IF NOT EXISTS`)
**Size:** S (~130 LOC)
**Dependencies:** TASK-001
**Notes:** Include operator runbook note in docstring about manual RLS enablement as `klai` superuser.

### TASK-003: Key generation + SHA-256 hashing utility
**Files:** `klai-portal/backend/app/services/partner_keys.py` (new)
**Functions:** `generate_partner_key() -> tuple[str, str]` returns `(plaintext, sha256_hex)`; `verify_partner_key(plaintext, hash) -> bool` using `hmac.compare_digest`
**Requirement:** REQ-1.1, non-functional privacy
**Test:** format `pk_live_` + 40 hex chars, hash is 64 hex chars, two generated keys differ, constant-time compare
**Size:** S (~30 impl + 50 test)
**Dependencies:** none

### TASK-004: Redis sliding-window rate limiter
**Files:** `klai-portal/backend/app/services/partner_rate_limit.py` (new)
**Function:** `async def check_and_increment(key_id: str, limit_per_minute: int) -> tuple[bool, int]` using Redis ZSET
**Requirement:** REQ-2.4
**Test:** under limit allows, at limit denies, window slides forward correctly, returns correct `retry_after`
**Size:** M (~80 impl + 100 test)
**Dependencies:** none (uses existing `redis_client.get_redis_pool`)

### TASK-005: Partner auth dependency (`get_partner_key`)
**Files:** `klai-portal/backend/app/api/partner_dependencies.py` (new)
**Returns:** `PartnerAuthContext` dataclass with `key_id`, `org_id`, `zitadel_org_id`, `permissions` dict, `kb_access` mapping `{kb_id: access_level}`, `rate_limit_rpm`
**Requirement:** REQ-2.1, REQ-2.2, REQ-2.4, REQ-2.6, REQ-1.6
**Test:** 7 cases — missing header → 401, malformed prefix → 401, unknown hash → 401, inactive key → 401 (same message), valid key happy path, rate-limit exceeded → 429 with Retry-After, `last_used_at` update scheduled
**Size:** M (~130 impl + 180 test)
**Dependencies:** TASK-001, TASK-003, TASK-004

### TASK-006: Permission + KB-scope enforcement helpers
**Files:** `klai-portal/backend/app/api/partner_dependencies.py` (same file, added in second cycle)
**Functions:** `require_permission(auth, name)`, `require_kb_access(auth, kb_ids, level)` returning validated list or raising 403 with generic message
**Requirement:** REQ-2.3, REQ-2.5
**Test:** 6 cases — permission granted, permission missing → 403, all KBs in scope, one out of scope → 403 (generic message, no leak), empty requested falls back to key default, read vs read_write level check
**Size:** S (~60 impl + 100 test)
**Dependencies:** TASK-005

### TASK-007: Partner router skeleton + `GET /partner/v1/knowledge-bases`
**Files:** `klai-portal/backend/app/api/partner.py` (new), `klai-portal/backend/app/main.py` (register router)
**Requirement:** REQ-4.1
**Test:** returns only KBs in `auth.kb_access`, joined with `PortalKnowledgeBase` on `(org_id, id)` for name/slug; KBs not in scope absent; `chat` OR `knowledge_append` permission accepted
**Size:** S (~80 impl + 100 test)
**Dependencies:** TASK-005, TASK-006

### TASK-008: Chat completions service (retrieval + prompt building) — non-streaming
**Files:** `klai-portal/backend/app/services/partner_chat.py` (new)
**Requirement:** REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.5 (non-stream subset), REQ-3.6
**Test:** 8 cases — invalid model → 400, empty messages → 400, system-only messages → 400, KB out of scope → 403, retrieval timeout → 502, happy path returns OpenAI-shaped JSON, retrieval log scheduled async, `kb_id`→`kb_slug` translation correct
**Size:** L (~180 impl + 200 test) — may split into 008a (service helpers) + 008b (route handler)
**Dependencies:** TASK-007
**Reference:** `deploy/litellm/klai_knowledge.py` lines 60-345 for retrieval context building

### TASK-009: Chat completions streaming path (SSE)
**Files:** `klai-portal/backend/app/services/partner_chat.py`, `klai-portal/backend/app/api/partner.py`
**Requirement:** REQ-3.4
**Test:** mock LiteLLM streaming response as async iterator; assert `text/event-stream` content type, first chunk streams, `[DONE]` terminator forwarded, retrieval log fires once on stream close
**Size:** M (~80 impl + 120 test)
**Dependencies:** TASK-008

### TASK-010: `POST /partner/v1/feedback`
**Files:** `klai-portal/backend/app/api/partner.py` (new route), `klai-portal/backend/app/services/partner_feedback.py` (new, adapts existing feedback logic)
**Requirement:** REQ-5.1, REQ-5.2, REQ-5.3, REQ-5.4
**Test:** 5 cases — rating validation, feedback permission denied → 403, correlated path triggers quality update, uncorrelated path no update, idempotent duplicate returns 200 without new row
**Size:** M (~120 impl + 180 test)
**Dependencies:** TASK-007
**Reference:** `app/api/internal.py:422-510` for existing feedback pipeline

### TASK-011: `POST /partner/v1/knowledge` (append-only)
**Files:** `klai-portal/backend/app/api/partner.py` (new route), `klai-portal/backend/app/services/partner_knowledge.py` (new, ingest-api proxy)
**Requirement:** REQ-4.2, REQ-4.3, REQ-4.4
**Test:** 5 cases — `knowledge_append` permission missing → 403, `kb_id` not in scope → 403, `kb_id` in scope but only `read` → 403, >10MB content → 413, happy path proxies to `POST /ingest/v1/document` with translated `kb_slug` and returns `{knowledge_id, chunks_created, status}`
**Size:** M (~100 impl + 130 test)
**Dependencies:** TASK-007
**Notes:** Response mapping: ingest-api returns `{"status": "ok", "chunks": N, "artifact_id": ...}` → partner gets `{"knowledge_id": artifact_id, "chunks_created": chunks, "status": "ingested"}`

### TASK-012: Admin integrations model helpers + backend endpoints (POST/GET list)
**Files:** `klai-portal/backend/app/api/admin_integrations.py` (new), `klai-portal/backend/app/services/admin_integrations.py` (new)
**Requirement:** REQ-6.1, REQ-6.2, REQ-6.3, REQ-6.7 (partial — create/list events)
**Test:** non-admin → 403, create returns plaintext key once + metadata, create with out-of-org `kb_id` → 400, list excludes plaintext, junction table populated correctly, product events emitted
**Size:** M (~150 impl + 180 test)
**Dependencies:** TASK-001, TASK-003
**Auth:** Uses existing Zitadel `_require_admin` or equivalent role check

### TASK-013: Admin integrations detail/update/revoke endpoints
**Files:** `klai-portal/backend/app/api/admin_integrations.py` (add routes)
**Requirement:** REQ-6.4, REQ-6.5, REQ-6.6, REQ-6.7 (updated/revoked events), REQ-1.4
**Test:** detail returns full metadata with KB list, patch partial fields works, patch kb_access replaces rows atomically, revoke sets active=false, revoked key cannot be un-revoked, product events emitted
**Size:** M (~120 impl + 150 test)
**Dependencies:** TASK-012

### TASK-014: Frontend — Integrations list view + create flow
**Files:**
- `klai-portal/frontend/src/routes/admin/integrations/index.tsx` (new, list)
- `klai-portal/frontend/src/routes/admin/integrations/new.tsx` (new, create form)
- `klai-portal/frontend/src/routes/admin/integrations/_components/IntegrationsTable.tsx` (new)
- `klai-portal/frontend/src/routes/admin/integrations/_components/CreatedKeyModal.tsx` (new)
- `klai-portal/frontend/src/api/integrations.ts` (new API client)
- `klai-portal/frontend/messages/en.json`, `nl.json` (i18n strings)
- `klai-portal/frontend/src/routes/admin/-components/AdminNav.tsx` (add nav entry)
**Requirement:** REQ-6.8, REQ-6.9
**Test:** component tests for table render, form submission, modal display; Playwright smoke test for full create flow
**Size:** L (~400 LOC + tests)
**Dependencies:** TASK-012

### TASK-015: Frontend — Integrations detail/edit view
**Files:**
- `klai-portal/frontend/src/routes/admin/integrations/$id.tsx` (new, detail/edit)
- `klai-portal/frontend/src/routes/admin/integrations/_components/KbAccessEditor.tsx` (new, per-KB radio select)
- `klai-portal/frontend/src/routes/admin/integrations/_components/RevokeConfirmDialog.tsx` (new)
- Additional i18n strings
**Requirement:** REQ-6.8, REQ-6.9
**Test:** component tests for editor, patch submission, revoke confirmation; Playwright smoke test for edit + revoke flow
**Size:** M (~300 LOC + tests)
**Dependencies:** TASK-013, TASK-014

### TASK-016: Caddy routing
**Files:** `deploy/caddy/Caddyfile`
**Requirement:** SPEC environment section
**Test:** `docker compose exec caddy caddy validate` passes; manual curl smoke test post-deploy
**Size:** S (~15 LOC)
**Dependencies:** TASK-007 deployed
**Notes:** Reverse proxy to `portal-api:8010` (NOT 8000). Use `request_header` for `X-Request-ID` injection per `caddy.md` rule.

## Execution Order

**Serial dependencies:**
1. TASK-001 (model)
2. TASK-002 (migration) — can run while 003/004 in parallel
3. TASK-003, TASK-004 (utilities, independent)
4. TASK-005 (auth dependency, needs 001/003/004)
5. TASK-006 (scope helpers, needs 005)
6. TASK-007 (router skeleton)

**Parallel after TASK-007:**
- TASK-008 + TASK-009 (chat completions, the long pole)
- TASK-010 (feedback)
- TASK-011 (knowledge append)
- TASK-012 + TASK-013 (admin backend endpoints)

**Depends on admin backend:**
- TASK-014 (list + create UI, needs TASK-012)
- TASK-015 (detail UI, needs TASK-013 + TASK-014)

**Deployment:**
- TASK-016 (Caddy, last)

## Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| httpx | existing | Async HTTP client for LiteLLM + retrieval-api + ingest-api |
| Redis | existing | Rate limiting sliding window + feedback idempotency |
| SQLAlchemy | existing | Models |
| Alembic | existing | Migration |
| Mantine 8 | existing | UI components |
| @tanstack/react-router | existing | Routes |
| @tanstack/react-query | existing | Mutations/queries |
| Paraglide | existing | i18n |

**No new external dependencies.**

## Estimated Scope

- **New backend files:** ~10 (models, migration, services, routes, tests)
- **New frontend files:** ~8 (routes, components, API client)
- **Modified files:** ~5 (main.py, admin nav, Caddyfile, messages)
- **New DB tables:** 2 (`partner_api_keys`, `partner_api_key_kb_access`)
- **New alembic migrations:** 1
- **New API routes:** 10 (4 partner external + 6 admin internal)
- **Total tasks:** 16 atomic TDD cycles
