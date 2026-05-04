---
status: draft
version: 0.1.0
priority: P3
parent: SPEC-SEC-HYGIENE-001
title: knowledge-mcp /ingest per-tenant rate limit
---

# SPEC-INGEST-RATELIMIT-001

## Problem Statement

The knowledge-mcp `/ingest` endpoint currently lacks per-tenant rate limiting, allowing a single tenant to submit unlimited ingest requests. In multi-tenant environments, a misbehaving or resource-constrained tenant could exhaust ingest-processor queues and degrade service for other tenants. This SPEC defers the implementation of per-tenant rate limiting from SPEC-SEC-HYGIENE-001 (HY-47) to establish a focused, backlog-tracked specification.

Deferred from SPEC-SEC-HYGIENE-001 HY-47 — see that SPEC's progress.md mailer-slice section for context.

## Requirements (EARS Format)

### REQ-1: Per-Tenant Rate Limit Enforcement

The knowledge-mcp `/ingest` endpoint MUST enforce a per-tenant rate limit. 

- Initial target: 100 ingests per organization per hour
- Configuration via environment variable: `KNOWLEDGE_MCP_INGEST_RATE_LIMIT_PER_HOUR`
- Allows operators to adjust the limit without redeployment

### REQ-2: Redis-Based State Management

Rate-limit state MUST use Redis (not in-memory state) so it survives container restart and works across replicas.

- Rate-limit counters tracked per (org_id, hour_bucket)
- Counters expire after 1 hour
- Read-heavy workload optimized for fast lookups

### REQ-3: Fail-Closed on Redis Unavailability

Rate-limit MUST fail-CLOSED if Redis is unreachable, consistent with SPEC-SEC-INTERNAL-001 fail-mode discipline.

- If Redis is unavailable, `/ingest` returns 503 Service Unavailable
- Never fall back to in-memory or allow-all behavior
- Log the Redis outage for observability

### REQ-4: HTTP 429 Response with Retry-After Header

When a tenant exceeds the rate limit, the endpoint MUST respond with HTTP 429 Too Many Requests.

- Include `Retry-After` header indicating when the next request may succeed
- Response body: descriptive JSON error message
- Example: `Retry-After: 3600` (retry in 1 hour)

### REQ-5: Product Event Emission

Rate-limit hits MUST emit a `knowledge.ingest_rate_limited` product event for observability.

- Event payload includes: `org_id`, `user_id`, `endpoint`, `tenant_limit`, `tenant_current_count`
- Logged to `product_events` table (via portal-api or retrieval-api)
- Enables monitoring of tenant rate-limit behavior

## Acceptance Criteria

| Criterion | Assertion | Test |
|-----------|-----------|------|
| REQ-1 | Requests beyond 100/hour per org return 429 | POST 101 requests in 1 hour, verify 429 on request 101 |
| REQ-2 | Rate limit state persists across container restart | Restart knowledge-mcp pod mid-hour, verify counter maintained |
| REQ-3 | 503 returned if Redis unreachable | Simulate Redis failure, verify 503 on next request |
| REQ-4 | 429 response includes Retry-After header | Exceed limit, verify `Retry-After` header present and valid |
| REQ-5 | Product event emitted on rate limit | Exceed limit, verify `knowledge.ingest_rate_limited` in logs |

## Status

This SPEC is explicitly marked as **backlog** — not in active development. It remains a deferred task pending prioritization for implementation.

## History

### v0.1.0 (2026-04-29) — DRAFT
- Initial specification created from SPEC-SEC-HYGIENE-001 HY-47 deferral
- All 5 REQs defined in EARS format
- Acceptance criteria established
