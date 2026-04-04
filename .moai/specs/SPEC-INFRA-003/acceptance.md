# SPEC-INFRA-003: Acceptance Criteria

## R1: vexa-redis Authentication

**Given** vexa-redis is running with --requirepass
**When** a client connects without the correct password
**Then** the connection is rejected with NOAUTH

## R2: firecrawl-postgres SOPS Password

**Given** the deploy stack is running
**When** docker compose config firecrawl-postgres is inspected
**Then** the password is not the literal string firecrawl_pass

## R3: Billing Admin Check

**Given** a non-admin org member
**When** they call POST /api/billing/mandate
**Then** the response is 403 Forbidden

## R4: /metrics Restricted

**Given** an external request to https://*.getklai.com/metrics
**When** the request arrives at Caddy
**Then** the response is 404

## Quality Gates

- All existing tests pass
- Uptime Kuma monitors stay green after deploy
