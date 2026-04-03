---
id: SPEC-INFRA-003
version: "1.0.0"
status: completed
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: P2
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-02 | 1.0.0 | Initial draft from code review findings |
| 2026-04-02 | 1.1.0 | Implementation complete — all 5 requirements implemented |

# SPEC-INFRA-003: Infrastructure Security Hardening

## Overview

Address infrastructure security issues discovered during code review of deploy configs and backend endpoints. Focuses on service authentication, secrets management, and access control.

## Requirements (EARS Format)

### R1: Add Authentication to vexa-redis (MEDIUM)

**When** vexa-redis starts, **the system shall** require a password via `--requirepass ${VEXA_REDIS_PASSWORD}`.

**Rationale:** vexa-redis runs without authentication. Bot containers on the vexa-bots network can freely access and manipulate state.

**File:** `deploy/docker-compose.yml:811-825`

### R2: Move firecrawl-postgres Password to SOPS (MEDIUM)

**When** firecrawl-postgres starts, **the system shall** read its password from `${FIRECRAWL_DB_PASSWORD}` environment variable (sourced from SOPS-encrypted .env), replacing the hardcoded `firecrawl_pass`.

**File:** `deploy/docker-compose.yml:936-937, 977`

### R3: Add Admin Check to Billing Endpoints (LOW)

**When** a state-changing billing action is requested (create mandate, cancel subscription), **the system shall** verify the caller has the admin role.

**File:** `app/api/billing.py`

### R4: Restrict /metrics Endpoint (MEDIUM)

**When** a request arrives at `/metrics` from outside the Docker network, **the system shall** be blocked by Caddy.

**Files:** `deploy/caddy/Caddyfile`

### R5: Document CORS Regex as Security-Critical (LOW)

**When** the CORS `allow_origin_regex` is modified, **the system shall** have a code comment marking it as security-critical.

**File:** `app/main.py`

## Constraints

- vexa-redis password change requires updating all vexa bot connection strings
- firecrawl-postgres password change requires re-creating the database or ALTER USER
- Caddy changes require container restart (admin off)

## Implementation Notes

**Commits:**
- `b5e8497` — `fix(security): implement SPEC-INFRA-003 infrastructure security hardening`
- `45a7c32` — `fix(security): add vexa-redis password to REDIS_URL connection strings`

**Implementation summary:**
| Req | Status | Files Changed |
|-----|--------|---------------|
| R1 | Done | `docker-compose.yml` — `--requirepass ${VEXA_REDIS_PASSWORD}` on vexa-redis, auth-aware healthcheck |
| R2 | Done | `docker-compose.yml` — firecrawl-postgres and firecrawl-worker use `${FIRECRAWL_DB_PASSWORD}` |
| R3 | Done | `billing.py` — admin check via `_require_admin` on mandate/cancel endpoints |
| R4 | Done | `Caddyfile` — `handle /metrics { respond 404 }` block |
| R5 | Done | `config.py` — SECURITY-CRITICAL comment on CORS regex |

**Additional changes (R1 follow-up):**
- vexa-meeting-api and vexa-runtime-api `REDIS_URL` updated with `${VEXA_REDIS_PASSWORD}` auth (commit `45a7c32`)

**Deployment prerequisites:**
- Add `VEXA_REDIS_PASSWORD` to SOPS-encrypted `.env`
- Add `FIRECRAWL_DB_PASSWORD` to SOPS-encrypted `.env`
- Run `ALTER USER firecrawl PASSWORD '...'` on firecrawl-postgres (or re-create the container with fresh volume)
- Restart Caddy, vexa-redis, firecrawl-postgres, firecrawl-worker

**Behavior changes:**
- Billing error messages now return generic text instead of Moneybird exception details (R3 hardening)
- `_get_org` now returns `(org, user_id, caller_user)` tuple (3 values instead of 2)
