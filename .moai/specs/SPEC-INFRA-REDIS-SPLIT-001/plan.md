# SPEC-INFRA-REDIS-SPLIT-001 — Implementation Plan

## Approach

Migrate every klai service from a single `REDIS_URL` env var to per-component `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD`, `REDIS_DB`, `REDIS_SSL`. Phased rollout with backward-compat shim during migration so each service ships independently. Final cleanup PR removes the `REDIS_URL` setting after all services + SOPS are migrated.

The mailer rollout is the reference implementation: its existing `parse_redis_url` (PR #231 / SPEC-SEC-MAILER-INJECTION-001 REQ-6.5) plus the boot validator (PR #239 / REQ-6.5 fail-fast) form the working pattern. This SPEC replicates that pattern across the seven other services.

## Service Rollout Table

| # | Service | Settings file | Existing redis usage | Rollout PR ID (TBD) |
|---|---|---|---|---|
| 1 | klai-mailer | `klai-mailer/app/config.py` | nonce + rate-limit (REQ-6) | reference-impl (already shipped) |
| 2 | klai-knowledge-mcp | `klai-knowledge-mcp/config.py` | session cache | new |
| 3 | klai-portal-api | `klai-portal/backend/app/core/config.py` | RLS GUC cache, SSO state | new (highest blast radius — staged behind shim for 7d) |
| 4 | klai-retrieval-api | `klai-retrieval-api/retrieval_api/config.py` | retrieval result cache | new |
| 5 | klai-knowledge-ingest | `klai-knowledge-ingest/knowledge_ingest/config.py` | crawl-status pubsub | new |
| 6 | klai-scribe-api | `klai-scribe/scribe-api/app/config.py` | meeting-state cache | new |
| 7 | klai-connector | `klai-connector/app/config.py` | sync-job lock | new |
| 8 | klai-focus / research-api | `klai-focus/research-api/app/config.py` | notebook cache | new |

Each service rollout follows the SAME 5-stage pattern:

**Stage A** — PR adds per-component settings + shim that reads `REDIS_URL` if individual vars are unset; emits `redis_url_legacy_shim_used` WARNING. Lands first.

**Stage B** — Operator updates `klai-infra/core-01/.env.sops` to add per-component vars alongside the existing `REDIS_URL`. Both coexist; shim warning still fires.

**Stage C** — Operator removes `REDIS_URL` from SOPS once warning has stopped (typically next deploy after Stage B).

**Stage D** — Per-service follow-up PR removes the shim and the `REDIS_URL` setting after 7 consecutive days of no `redis_url_legacy_shim_used` warning. Settings becomes per-component-only.

**Stage E** (cross-cutting, last) — semgrep rule `no-redis-from-url` blocks any future `Redis.from_url(settings.redis_url)` reintroduction. New service workflow file added.

## Task Decomposition

| # | Task | Files | Risk |
|---|---|---|---|
| 1 | Extract `parse_redis_url` from `klai-mailer/app/redis_url.py` to a shared lib `klai-libs/redis-config/` | new lib + `klai-mailer/app/redis_url.py` reduced to a re-export | Medium — touches an already-shipped module; a re-export keeps the import path stable |
| 2 | Define the canonical `RedisSettings` mixin in the shared lib: per-component fields with the shim validator | `klai-libs/redis-config/` | Medium — the contract every service inherits |
| 3 | Per-service Stage-A PRs (×7) — each adds `RedisSettings` to the service's settings class, swaps `from_url` to kwargs, ships the shim | per service | Low individually; coordinate timing so a service is not deployed mid-rollout |
| 4 | Operator Stage-B + Stage-C — SOPS update across all services in one batch | `klai-infra/core-01/.env.sops` | Medium — wrong copy-paste between vars and SOPS values is the obvious risk; mitigate with a verification script in the migration runbook |
| 5 | Per-service Stage-D PRs (×7) — remove shim + legacy setting. Land in same week as the 7-day soak completes | per service | Low individually |
| 6 | Stage E — semgrep rule + workflow integration | new `rules/no-redis-from-url.yml`, every service workflow file | Low |

## Files Affected

- `klai-libs/redis-config/` — new shared lib (mirrors `klai-libs/log-utils`, `klai-libs/identity-assert` patterns)
- 8 service settings files (see Service Rollout Table)
- 8 service docker-compose entries — switch from `${REDIS_URL}` to the per-component vars
- `klai-infra/core-01/.env.sops` — Stage B + C
- `rules/no-redis-from-url.yml` (new semgrep rule) + 8 workflow files (each service's CI integrates the rule via `ast-grep/action`)
- New `docs/runbooks/redis-config-migration.md` — operator step-by-step for the SOPS migration with verification commands

## Technology Choices

- **Shared lib** over copy-paste in each service — one source of truth for the parser + shim. Same pattern as `klai-libs/log-utils`.
- **Per-component env vars** over a single SOPS-encoded URL — eliminates the URL-encoding-of-password failure class entirely. Matches 12-factor.
- **Boot validator on each per-component field** — non-empty `REDIS_HOST` and integer `REDIS_PORT`; password may be empty for unauth Redis (dev) but emit a startup WARNING in that case.
- **`redis-py 5.x` kwargs constructor** — already supported across all current klai services; no version bump required.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Stage-A shim hides a per-component env var typo (operator sets `REDIS_PORT=6739` but forgets, shim falls back to URL) | Stage-A explicitly logs the source of each value at startup: `redis_settings_source field=REDIS_HOST source=env|legacy_url`. Operators see exactly which path is active per field |
| Mid-rollout service A reads URL, service B reads per-component, with mismatched values | Solved by Stage B making BOTH consistent. The Stage-C cleanup happens only after a 7-day soak with both sources present |
| Forgetting a service in the rollout | Stage E semgrep rule fires CI failure on any future `Redis.from_url(...)` — even if a service is skipped today, the rule protects future regressions |
| Existing tests assume URL-style settings | Settings tests use the shim path; both URL-only and per-component-only configurations have parametrised tests in the shared lib |

## Success Criteria

- All 8 services pass their full test suites at every rollout stage.
- Zero production incidents during the rollout window (verified by absence of `mailer_zitadel_webhook_failed`, `caddy_5xx_count_high`, etc. firing during the migration weeks).
- After Stage E, semgrep CI rejects any PR that reintroduces `Redis.from_url(...)` against any klai service path.
- Operator-runbook `redis-config-migration.md` walks a fresh operator through the SOPS migration end-to-end without ambiguity.

## Out of Scope

- Migrating Redis itself (Redis Cluster, Sentinel, ElastiCache).
- Adding TLS / `rediss://` support to the broker. The schema accommodates `REDIS_SSL=true` for future use but the broker is unchanged.
- Changing the password rotation cadence. The rotation playbook becomes safer because URL-encoding of new passwords is no longer required, but the rotation policy itself stays in the existing credential-rotation runbook.

## Ordering & Branch Strategy

- **One PR per stage per service**, not one mega-PR. Smaller PRs review faster, fail-fast in CI, ship independently.
- **Mailer is already done** — use it as the reference. Other 7 services follow the same diff shape.
- **Portal-api is staged behind a 7-day soak** — highest blast radius. All other services finish Stage A → Stage D before portal-api starts Stage A.
- **Stage E (semgrep) lands ONLY after Stage D for all 8 services** — otherwise the rule false-fires on a service still in transition.
