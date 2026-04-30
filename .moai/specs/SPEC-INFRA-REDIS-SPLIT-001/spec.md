---
id: SPEC-INFRA-REDIS-SPLIT-001
version: "0.1.0"
status: draft
created: "2026-04-30"
updated: "2026-04-30"
author: MoAI
priority: medium
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-04-30 | MoAI | Stub created from SPEC-SEC-MAILER-INJECTION-001 v0.3.1 follow-up — replace `REDIS_URL` with `REDIS_HOST` + `REDIS_PASSWORD` across all klai services to eliminate the URL-encoding-of-password class of bugs that caused the 2026-04-29 mailer outage. |

# SPEC-INFRA-REDIS-SPLIT-001: Replace `REDIS_URL` with `REDIS_HOST` + `REDIS_PASSWORD` across klai services

## Overview

Eliminate the entire class of "operator forgot to URL-encode the Redis password" bugs by retiring the single `REDIS_URL` env var in favour of `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD`, `REDIS_DB`, and `REDIS_SSL` env vars across every klai service that uses Redis. Each variable carries one piece of information; no escaping ambiguity.

The 2026-04-29 mailer `/notify` 500 outage was caused by `redis_asyncio.from_url(settings.redis_url)` calling `urllib.parse.urlparse`, which fails on passwords with reserved characters (`:`, `/`, `+`, `@`). REQ-6.5 of SPEC-SEC-MAILER-INJECTION-001 v0.3.1 fixed it for mailer with a custom `parse_redis_url`, but the same bug class is latent in every other service that uses `from_url(REDIS_URL)`. Fixing one service at a time after each rotation is whack-a-mole; the structural fix is to remove the URL pattern.

This SPEC ALSO touches the deploy infrastructure (klai-infra repo): SOPS env files migrate from `REDIS_URL=redis://:pw@host:port/db` to the per-component variables. Rollout requires careful ordering across services to avoid partial-config breakage.

## Environment

- **Affected services** (all use Redis): klai-portal-api, klai-mailer, klai-retrieval-api, klai-knowledge-ingest, klai-scribe-api, klai-connector, klai-knowledge-mcp, klai-focus / research-api.
- **Affected infra:** klai-infra/core-01/.env.sops, deploy/docker-compose.yml.
- **Library:** redis-py 5.x — accepts both `from_url(...)` and `Redis(host=..., port=..., password=..., ...)` constructor patterns. The kwargs pattern is the target.
- **Existing fix anchor:** `klai-mailer/app/redis_url.py::parse_redis_url` — the structural URL parser introduced in REQ-6.5. This SPEC's exit criterion is "no service uses `parse_redis_url` because no service receives a URL anymore".

## Assumptions

- A1: All current services can be deployed with both the old `REDIS_URL` env var and the new per-component variables in parallel during the migration window. Pydantic settings allows defaulting one from the other so single-deploy cutovers are NOT required.
- A2: The Redis broker itself does not change — only the client-side configuration changes. Same hostname, same port, same password.
- A3: The current production password value continues to contain reserved characters that REQUIRE URL-encoding when used as a URL component. Operators will not be required to rotate the password as part of this migration.
- A4: SOPS files in klai-infra are updated by the operator (not Claude) per the existing `follow-loaded-procedures` rule.

## Requirements

### R1 — Ubiquitous: per-component env vars are the source of truth

Each klai service that uses Redis SHALL read individual `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD`, `REDIS_DB`, `REDIS_SSL` env vars via pydantic-settings, AND SHALL construct the Redis client via `redis_asyncio.Redis(host=..., port=..., username=..., password=..., db=..., ssl=...)` kwargs. No service SHALL call `redis_asyncio.from_url(...)`.

### R2 — Event-driven: backward-compat shim during migration

WHEN a service starts AND `REDIS_URL` is set AND any of the per-component vars is unset THEN the service SHALL parse `REDIS_URL` (using `parse_redis_url`) to fill in the missing per-component values. The shim SHALL emit a structlog WARNING (`redis_url_legacy_shim_used`) so operators see they are still on the legacy var.

### R3 — State-driven: deprecation removal after migration

IF the migration is complete (all SOPS files updated, all services redeployed, no `redis_url_legacy_shim_used` warnings observed for 7 consecutive days) THEN the legacy shim SHALL be removed in a follow-up commit and `REDIS_URL` SHALL no longer be a recognized setting.

### R4 — Unwanted Behavior: prevent reintroduction of `from_url(...)`

A semgrep rule SHALL block any new code introducing `redis_asyncio.from_url(...)` or `redis.Redis.from_url(...)` in `klai-*/`. The rule MAY be bypassed in test fixtures via `# nosemgrep` with a justifying comment.

### R5 — Optional: per-service Redis health-check endpoint

Where a service exposes a `/health` endpoint, the health check MAY include a Redis ping (with a short timeout) so that the next time Redis configuration drifts, the operator sees `503` on `/health` IMMEDIATELY at deploy rather than waiting for the next request to fail.

## Specifications

### Per-component env var schema

```
REDIS_HOST=redis           # required, no default
REDIS_PORT=6379            # default 6379
REDIS_USERNAME=            # default empty (compatible with default ACL)
REDIS_PASSWORD=<password>  # required if Redis is auth-enabled
REDIS_DB=0                 # default 0
REDIS_SSL=false            # default false; true → use rediss:// equivalent
```

### Service-by-service rollout

| # | Service | Owner | Notes |
|---|---|---|---|
| 1 | klai-mailer | done (REQ-6.5 already uses kwargs path internally; switch settings to per-component) | smallest blast radius, validate the pattern |
| 2 | klai-knowledge-mcp | — | next — no transactional state |
| 3 | klai-portal-api | CRITICAL | rollback script must be tested first |
| 4 | klai-retrieval-api | — | |
| 5 | klai-knowledge-ingest | — | |
| 6 | klai-scribe-api | — | |
| 7 | klai-connector | — | |
| 8 | klai-focus / research-api | — | |

Each service rollout follows: (a) PR adding per-component settings + shim, (b) deploy with shim active, (c) update SOPS to per-component vars, (d) re-deploy, (e) verify shim warning is gone, (f) (after all services) PR removing the shim and the `REDIS_URL` setting.

### Semgrep rule (REQ-4)

```yaml
rules:
  - id: no-redis-from-url
    pattern-either:
      - pattern: redis_asyncio.from_url(...)
      - pattern: redis.asyncio.from_url(...)
      - pattern: $REDIS.Redis.from_url(...)
    message: |
      `Redis.from_url(url)` calls `urllib.parse.urlparse`, which fails on
      passwords with reserved characters. Use individual env vars +
      `Redis(host=..., port=..., password=..., ...)` kwargs. See
      SPEC-INFRA-REDIS-SPLIT-001.
    languages: [python]
    severity: ERROR
```

## Files Affected

- Every klai service's `app/config.py` (or equivalent) — add per-component settings, add shim.
- Every klai service's Redis client construction site (use CodeIndex `query "Redis client construction"` to enumerate).
- `klai-infra/core-01/.env.sops` — replace `REDIS_URL=...` with per-component vars.
- `deploy/docker-compose.yml` — pass per-component vars in each service environment block.
- New `rules/no-redis-from-url.yml` (semgrep) + workflow integration.

## MX Tag Plan

- Each service's Redis client constructor becomes `# @MX:ANCHOR` after refactor (fan_in increases as the kwargs path becomes the only constructor).
- `parse_redis_url` in mailer (REQ-6.5) is `@MX:ANCHOR` — gets a `# @MX:NOTE: deprecated post-migration` after R3 completes.

## Exclusions

- Migrating Redis itself (e.g. Redis Cluster, Redis Sentinel, ElastiCache) — out of scope; this SPEC only changes how clients address the existing single Redis broker.
- Adding TLS / `rediss://` support to the broker — out of scope; only the env var schema accommodates `REDIS_SSL=true` for future use.
- Changing the password rotation cadence — this SPEC removes the URL-encoding-of-password class of bug, which makes rotation safer, but does not enforce a rotation policy.

## Implementation Notes (for `/moai run`)

- Use the mailer rollout (#1) as the reference implementation. Its `parse_redis_url` already handles the messy URL form; the SPEC's R2 shim can re-use that helper.
- Stage 1 PR per service is small (config schema + shim + tests). Stage 2 is the SOPS update (operator action). Stage 3 is the cleanup PR (post-7-day soak).
- Add a CI check that fails if both `REDIS_URL` and `REDIS_HOST` are set in the same env file — they are mutually exclusive after the shim is removed.
