---
id: SPEC-INFRA-003
version: "1.0.0"
status: draft
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: P2
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-02 | 1.0.0 | Initial draft from code review findings |

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
