# SPEC-TI-006-007-FOLLOWUP-001 — Close webhook-replay wiring + ownership gaps

**Predecessor:** PR #380 (closed, branch `feature/SPEC-TI-006-007-WEBHOOK-REPLAY-GITEA`).
SPEC-TI-006 (Moneybird/Vexa/Gitea webhook replay-protection) and SPEC-TI-007
(Gitea webhook hardening) stayed unmerged because adversarial audit on PR #380
surfaced four BLOCKER findings + one LOW.
**Pitfalls:** `fail-open-auth` (HIGH), `empty-secret-fail-open` (HIGH),
`alembic-cannot-drop-non-portal_api-tables` (HIGH),
`validator-env-parity` (HIGH).
**Priority:** HIGH — without these fixes, Gitea webhooks 503 the moment
SPEC-TI-006/007 land (Redis network unreachable) and the Gitea
replay-check is bypassable by simply omitting one header.
**Status:** Ready

## Goal

Close the four wiring/ownership/fail-open bugs PR #380's audit found, plus one
consistency gap on portal-api config validation, so SPEC-TI-006 and SPEC-TI-007
can be re-opened and actually deliver replay-protection on
Gitea/Moneybird/Vexa webhooks. All five fixes are mechanical: compose-network
membership, header REQUIRED guard, migration-ownership step, fail-loud DB
exception class, and a `_require_*` field validator.

## Acceptance criteria

- **AC-1** `deploy/docker-compose.yml`: knowledge-ingest joins
  `net-redis` and adds `depends_on: redis` (alongside its existing
  `depends_on: postgres`). Mirror the retrieval-api pattern at
  lines ~820-823. Without this, `REDIS_URL` resolves to a hostname
  unreachable on knowledge-ingest's networks (`net-redis` is
  `internal: true`) and every Gitea webhook 503s on the first
  nonce-store call.

- **AC-2** Gitea handler in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`
  REQUIRES the `X-Gitea-Delivery` header. A request without it (or
  with a whitespace-only value) returns **400 Bad Request** with
  body `{"detail": "missing X-Gitea-Delivery header"}`. The current
  `if delivery_id:` short-circuit (lines 656-657) is removed —
  replay check ALWAYS runs against `delivery_id`, never silently
  skipped. Class: `fail-open-auth`.

- **AC-3** The existing test `test_missing_delivery_id_skips_replay_check`
  is renamed to `test_missing_delivery_id_returns_400` and inverted:
  it asserts 400, not 200. The previous test locked in the bug as
  "desired behavior" — leaving it would re-introduce the gap.
  Add a sibling `test_empty_delivery_id_returns_400` covering the
  whitespace-only header case (`X-Gitea-Delivery: "   "`).

- **AC-4** Migration `0002_gitea_repo_to_org.py` adds
  `op.execute("ALTER TABLE knowledge.gitea_repo_to_org OWNER TO klai;")`
  immediately after the `op.create_table(...)` call. Without this,
  the alembic role becomes the table owner and the post-deploy SQL
  `post_deploy_0002_gitea_repo_to_org.sql` fails with
  `InsufficientPrivilegeError: must be owner of table` on the
  `ENABLE ROW LEVEL SECURITY` step. Same recovery class as the
  `alembic-cannot-drop-non-portal_api-tables` pitfall.

- **AC-5** `_get_org_id_from_db` in `routes/ingest.py:843-855`
  distinguishes "repo not found" from "DB error":
  - `asyncpg.exceptions.UndefinedTableError` → re-raise as a new
    `ConfigurationError` (HTTP 500 + structured log
    `gitea_org_lookup_table_missing` at ERROR level).
  - Empty result row → return `None` (caller maps to 200
    `{"status": "ignored"}` as today).
  - Any other `Exception` → re-raise as `ConfigurationError`
    (HTTP 500 + ERROR log).

  The current bare `except Exception: return None` collapses missing
  table, broken pool, RLS denial, and "unknown repo" into one
  silent `200 ignored`. Class: `fail-open-auth`.

- **AC-6** `klai-portal/backend/app/core/config.py:184` (`redis_url`)
  gets a `_require_redis_url` field-validator matching the
  `_require_moneybird_webhook_token` style. Empty / whitespace-only
  / missing → `ValidationError` at Settings load (BEFORE FastAPI
  lifespan, so misconfigured deploys fail-fast instead of returning
  503 on first webhook). Test in `tests/test_redis_url_validator.py`
  covers reject (empty, whitespace) and accept cases. Class:
  `validator-env-parity`.

- **AC-7** A CI smoke-test (added to the `knowledge-ingest.yml`
  workflow, or an integration test in `klai-knowledge-ingest/tests/`)
  verifies that knowledge-ingest, started with deploy-compose.yml
  network membership applied, can `redis.ping()` against the
  `net-redis`-attached redis service. Closes the gap that AC-1 alone
  could regress on a future compose edit.

## Background

PR #380 wired `klai-libs/webhook-replay` correctly on portal-api
(Moneybird, Vexa) but the audit found three classes of bug on
knowledge-ingest (Gitea):

1. **Network isolation** (AC-1): `net-redis` has `internal: true`.
   Services not on the network cannot resolve the hostname through
   Docker's embedded DNS. Connection refused, webhook 503s.
2. **Header fail-open** (AC-2/3): Moneybird/Vexa nonce parts come
   from payload fields (`event_id`, `vexa_meeting_id`, `timestamp`)
   that are always present. Gitea uses a HEADER an attacker
   controls. Empty header silently bypassed replay-protection,
   leaving HMAC as the only defense. The test that locked this in
   was itself the bug.
3. **DB-error fail-open** (AC-5): `_get_org_id_from_db` swallowed
   any exception as "unknown repo → 200 ignored". Combined with
   AC-4's failure mode, a deploy could appear green while no Gitea
   event ever landed.

AC-4 prevents the migration-ownership trap: post-deploy SQL ships
`ENABLE RLS`, which requires table-owner privileges, but the alembic
role is not `klai`. AC-6 is consistency hygiene: every other
auth-relevant field in `config.py` already has a `_require_*`
validator; `redis_url` was the gap.

## Operator step (after merge)

No manual step beyond standard deploy. Post-merge verification:

```bash
# AC-1 — knowledge-ingest can reach redis
ssh core-01 "docker exec klai-core-knowledge-ingest-1 \
  python -c 'import redis,os; redis.from_url(os.environ[\"REDIS_URL\"]).ping()'"

# AC-4 — table owner is klai
ssh core-01 "docker exec klai-core-postgres-1 \
  psql -U klai -d klai -c \"SELECT tableowner FROM pg_tables \
  WHERE tablename='gitea_repo_to_org';\""
# Expect: klai

# AC-2 — empty header rejected
curl -X POST https://api.getklai.com/webhooks/gitea -d '{}' -i
# Expect: HTTP/1.1 400 Bad Request
```

## Out of scope

- Re-implementing SPEC-TI-006 / SPEC-TI-007 in full. This SPEC closes
  the four BLOCKER + one LOW from PR #380; the predecessor SPECs are
  re-opened on a fresh branch that includes these fixes.
- Moneybird/Vexa flows on portal-api — already wired correctly on
  PR #380 (portal-api is on `net-redis`). Only AC-6 touches
  portal-api.
- Lifecycle hook for nonce store init at startup — covered indirectly
  if AC-7 passes.
- Migrating knowledge-ingest off the `klai` superuser DSN —
  `SPEC-TI-011-PER-SERVICE-DB-ROLES`. AC-4 works regardless of which
  non-superuser role runs alembic, because the explicit `OWNER TO
  klai` step runs while the migration is still executing as the
  alembic role and Postgres allows ownership transfers FROM the
  current owner.
