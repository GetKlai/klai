---
id: SPEC-BACKEND-001
version: "1.0.0"
status: completed
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: P1
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-02 | 1.0.0 | Initial draft from code review findings |
| 2026-04-02 | 1.1.0 | Implementation complete — R5 dropped (not applicable), R1-R4/R6-R10 implemented |

# SPEC-BACKEND-001: Backend Architecture Hardening

## Overview

Address backend architecture issues discovered during comprehensive code review of klai-portal/backend. Focuses on database resilience, performance, API consistency, and security hardening.

## Requirements (EARS Format)

### R1: Database Connection Pool Configuration (CRITICAL)

**When** portal-api starts, **the system shall** configure the SQLAlchemy async engine with `pool_size=10`, `max_overflow=20`, `pool_recycle=3600`, and `pool_pre_ping=True`.

**Rationale:** Current engine has no pool configuration. Missing `pool_pre_ping` causes "connection already closed" errors after PostgreSQL restarts.

**File:** `app/core/database.py:13`

### R2: Zitadel Userinfo Caching (HIGH)

**When** an authenticated request arrives, **the system shall** cache the Zitadel userinfo response with a 60-second TTL keyed on the access token, **so that** subsequent requests within the TTL window do not make a Zitadel roundtrip.

**Rationale:** Currently every authenticated request makes an HTTP call to Zitadel userinfo (+50-200ms latency).

**Files:** `app/api/dependencies.py`, `app/api/auth.py`

### R3: Fix update_group products=[] Bug (HIGH)

**When** a group is updated via `PUT /api/groups/{id}`, **the system shall** return the actual products assigned to the group in the response.

**Rationale:** Currently returns hardcoded `products=[]` regardless of actual assignments.

**File:** `app/api/groups.py` (~line 115-120)

### R4: Consolidate Auth Helpers (HIGH)

**When** any endpoint needs caller org context, **the system shall** use the single `_get_caller_org()` from `app/api/dependencies.py`, **so that** auth logic is not duplicated across 5 files.

**Files to consolidate:** `billing.py:_get_org()`, `mcp_servers.py:_get_caller_org()`, `me.py` (inline), `meetings.py:_get_user_and_org()`

### R5: Pin LibreChat Image Tag (HIGH)

**When** portal-api provisions a LibreChat container, **the system shall** use a pinned image tag (not `:latest`).

**File:** `app/core/config.py` — `librechat_image` default

### R6: Database-Level Pagination (MEDIUM)

**When** listing groups, knowledge bases, or meetings, **the system shall** accept `limit` and `offset` query parameters and apply them at the SQL query level.

**Endpoints:** `GET /api/groups`, `GET /api/knowledge-bases`, `GET /api/bots/meetings`

### R7: Fix Blocking File I/O (MEDIUM)

**When** loading the MCP server catalog, **the system shall** use `asyncio.to_thread()` or cache the result at startup, **so that** synchronous `open()` + `yaml.safe_load()` does not block the event loop.

**File:** `app/api/mcp_servers.py:_load_catalog()` (~line 38-44)

### R8: Logging Context Before Response (MEDIUM)

**When** an authenticated request is processed, **the system shall** bind `org_id` and `user_id` to the structlog context **before** the route handler executes.

**Rationale:** Currently bound after `call_next()`, so all request-time logs miss this context.

**File:** `app/middleware/logging_context.py:22-28`

### R9: Sanitize Error Details in Responses (MEDIUM)

**When** an internal service error occurs (e.g., Moneybird API failure), **the system shall** log the full exception and return a generic error message to the client.

**Rationale:** `billing.py:143-144` leaks internal exception details in HTTP responses.

### R10: Increase Password Minimum Length (LOW)

**When** a user signs up, **the system shall** enforce a minimum password length of 12 characters.

**File:** `app/api/signup.py:44`

## Constraints

- No breaking API changes — pagination parameters must be optional with sensible defaults
- Auth helper consolidation must preserve all existing endpoint behavior
- Connection pool values must be configurable via environment variables

## Acceptance Criteria

See `acceptance.md` for Given/When/Then scenarios.

## Implementation Notes

**Commit:** `a839ec0` — `refactor(backend): consolidate auth dependencies and harden access control`

**R5 dropped:** LibreChat image is not provisioned by portal-api; not applicable.

**Implementation summary:**
| Req | Status | Files Changed |
|-----|--------|---------------|
| R1 | Done | `database.py`, `config.py` — pool config from env vars |
| R2 | Done | `zitadel.py` — 60s TTL dict cache with eviction at 500 entries |
| R3 | Done | `groups.py` — explicit product query after create/update |
| R4 | Done | `billing.py`, `mcp_servers.py`, `meetings.py` — consolidated to `_get_caller_org` |
| R5 | Dropped | Not applicable |
| R6 | Done | `access.py`, `meetings.py` — DB-level LIMIT/OFFSET + count query |
| R7 | Done | `mcp_servers.py` — `asyncio.to_thread()` for YAML loading |
| R8 | Done | `dependencies.py` — structlog `bind_contextvars` in `_get_caller_org` |
| R9 | Done | `billing.py` — generic error message, full exception logged |
| R10 | Done | `signup.py` — password minimum 8→12 |

**Behavior changes:**
- `meetings.py` now returns 404 (via `_get_caller_org`) instead of empty list when user has no org
- Password minimum increased from 8 to 12 characters (new signups only)

**Verified via Playwright:** Groups, Billing, Scribe, Users, Integrations pages all load correctly after deploy.
