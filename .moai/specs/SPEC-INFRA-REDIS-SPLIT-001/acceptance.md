# SPEC-INFRA-REDIS-SPLIT-001 — Acceptance Criteria

Six Given/When/Then scenarios. The SPEC is acceptable when all six
hold for every klai service that uses Redis.

## AC-1 — per-component settings construct cleanly

- **Given** a service's `Settings()` is instantiated with all six
  `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD`,
  `REDIS_DB`, `REDIS_SSL` env vars set,
- **When** the service starts up,
- **Then** `Settings()` constructs without raising; the resulting
  Redis client connects on first use; no `redis_url_legacy_shim_used`
  WARNING is emitted.

**Test target:** every service's `tests/test_config_*.py`

## AC-2 — Stage-A shim falls back to legacy URL with a warning

- **Given** only `REDIS_URL` is set in the env (per-component vars
  unset) — the Stage-A "operator hasn't migrated SOPS yet" state,
- **When** the service starts up under Stage A code,
- **Then** the shim parses `REDIS_URL` via `parse_redis_url`,
  populates the per-component fields from the parsed result,
  emits a structlog WARNING `redis_url_legacy_shim_used` with the
  service name, AND the resulting Redis client connects normally.

**Test:** `klai-libs/redis-config/tests/test_legacy_url_shim.py`

## AC-3 — both sources present: per-component wins, warning still fires

- **Given** BOTH `REDIS_URL` AND the per-component vars are set
  (the Stage-B "operator migrated SOPS, both coexist" state),
- **When** the service starts up,
- **Then** the per-component vars take precedence (verified by
  asserting the active host/port match the per-component values
  even when the URL would have parsed to different values),
  AND the shim still emits the WARNING — Stage-C cleanup is what
  removes the warning.

**Test:** `klai-libs/redis-config/tests/test_legacy_url_shim.py`

## AC-4 — Stage-D code path: legacy URL alone refuses boot

- **Given** the service has shipped Stage-D (shim removed, legacy
  setting deleted) AND only `REDIS_URL` is set in the env (operator
  forgot Stage C),
- **When** the service starts up,
- **Then** boot fails with a clear pydantic ValidationError naming
  the missing `REDIS_HOST` field (NOT a cryptic Redis connection
  error). The operator sees the misconfiguration in deploy logs
  immediately.

**Test:** every service's `tests/test_config_*.py` post-Stage-D

## AC-5 — semgrep rule blocks `Redis.from_url(...)` reintroduction

- **Given** a developer's PR reintroduces `redis.asyncio.Redis.from_url(settings.redis_url)`
  in any klai service path under `klai-*`,
- **When** the PR's CI runs the `no-redis-from-url` rule via
  `ast-grep/action`,
- **Then** the CI step exits non-zero with the `no-redis-from-url`
  message pointing at this SPEC. PR cannot merge.

**Test:** `rules/no-redis-from-url.yml` regression fixture in the
project's existing semgrep test harness

## AC-6 — runbook walks an operator through a SOPS migration

- **Given** a fresh operator with no prior context AND
  `docs/runbooks/redis-config-migration.md`,
- **When** they follow the runbook step by step on a staging
  environment that mirrors production SOPS,
- **Then** they complete Stage B + Stage C for at least one service
  in under 30 minutes, AND the verification script at the end of the
  runbook reports zero `redis_url_legacy_shim_used` warnings within
  five minutes after Stage C.

**Test:** runbook itself; manual verification on a staging
environment before main rollout begins.

## Run Acceptance Aggregate

The SPEC is **acceptable** when:

- AC-1 through AC-6 all hold for every service after its Stage D.
- All eight services have completed Stage D in the planned rollout
  window (target: one calendar quarter).
- `mailer_zitadel_webhook_failed` and equivalent service alerts have
  ZERO firings caused by Redis configuration during the migration.
- The semgrep rule `no-redis-from-url` is in CI for every klai
  service workflow.
- `klai-infra/core-01/.env.sops` contains zero `REDIS_URL=` entries
  after the rollout completes.
