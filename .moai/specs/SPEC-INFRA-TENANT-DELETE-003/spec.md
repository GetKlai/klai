---
id: SPEC-INFRA-TENANT-DELETE-003
version: "0.1.0"
status: ready-for-run
created: "2026-05-13"
updated: "2026-05-13"
author: Mark Vletter
priority: high
related:
  - SPEC-INFRA-TENANT-DELETE-001 (16-step orchestrator — DEPLOYED, broken in prod)
  - SPEC-INFRA-TENANT-DELETE-002 (G3 + G6 wipe-via-internal-endpoint siblings — DEPLOYED)
  - SPEC-INFRA-CONFIG-SYNC-001 (bind-mount-config-sync auto-rsync — DEPLOYED)
discovered_during: SPEC-E2E-PROD-TENANT preparation (this session, 2026-05-13)
---

# SPEC-INFRA-TENANT-DELETE-003: Fix deprovisioning — Meilisearch network + jsonb encode

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-13 | Mark Vletter | Initial. Two bugs found while attempting first real-world tenant deprovisioning. Both bugs make `SPEC-INFRA-TENANT-DELETE-001` orchestrator unusable for production tenants when invoked from portal-api. |

## Context

While preparing the SPEC-E2E-PROD-TENANT prerequisites (creating an `e2e@getklai.com` test tenant), the new tenant (org_id=10, slug=`e2e-37271947`) had to be deprovisioned to retest the signup flow. The first run of `deprovision_tenant(org_id=10, deprovisioner_type='system')` failed on step 6 (`_delete_meilisearch_index`) and the failure-reporting itself crashed before writing `portal_orgs.last_failure`.

This is the first real-world execution of the SPEC-INFRA-TENANT-DELETE-001 orchestrator against a production tenant. Six earlier `portal_orgs` hard-deletes exist in production (visible via the ID-gap 2–7 between `getklai`/`voys`/`test`/`e2e`), but those happened before the orchestrator landed — likely via `klai`-superuser manual cleanup. The orchestrator has never successfully completed end-to-end on production.

## Bugs

### Bug A — `_delete_meilisearch_index` cannot reach Meilisearch (CRIT)

**Symptom:**
```
asyncpg-side trace (top of stack):
sqlalchemy.exc.... InsufficientPrivilegeError: new row violates row-level security policy
... wait no — that was templates. For deprovisioning step 6:

httpx.ConnectError: [Errno -3] Temporary failure in name resolution
http://meilisearch:7700
```

**Verification (run on core-01 2026-05-13 14:55 UTC):**
```bash
docker exec klai-core-portal-api-1 getent hosts meilisearch
# (no output — DNS fails)
docker inspect klai-core-portal-api-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
# klai-net klai-monitoring klai-net-postgres klai-net-mongodb klai-net-redis klai-socket-proxy
# (no klai-net-meilisearch)
docker inspect klai-core-meilisearch-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
# klai-net-meilisearch
```

**Root cause:**
[`deploy/docker-compose.yml`](../../deploy/docker-compose.yml) portal-api `networks:`-block lists only 5 networks; `net-meilisearch` is absent. The Meilisearch container is reachable from LibreChat-tenants (which `_start_librechat_container` explicitly joins to `klai-net-meilisearch` at provisioning time — see `infrastructure.py:384`), but portal-api itself was never added.

The SPEC-INFRA-TENANT-DELETE-001 implementation added `_delete_meilisearch_index` as step 6 of the orchestrator without verifying portal-api could actually reach Meilisearch. The step's `meili_url = "http://meilisearch:7700"` line worked in dev-stack tests (different network topology) but always fails on production.

**Why not detected earlier:**
1. Six earlier hard-deletes happened via `klai`-superuser bypassing the orchestrator.
2. No production deprovisioning ran end-to-end since SPEC-INFRA-TENANT-DELETE-001 deployed.
3. Step 6 is *non-fatal-soft* on idempotent-skip (404), so a test against an empty Meili (no index for the test tenant) might have passed even with a working network — masking the network bug for any future "luck" runs.

### Bug B — `_mark_failed` cannot serialize dict to jsonb (HIGH)

**Symptom:**
```
sqlalchemy.exc.DBAPIError:
asyncpg.exceptions.DataError: invalid input for query argument $1:
{'step': '_delete_meilisearch_index', 'error': ..., 'attempt': 3,
 'failed_at': '2026-05-13T14:56:58+00:00'}
('dict' object has no attribute 'encode')
```

**Verification:**
```sql
SELECT id, slug, provisioning_status, last_failure FROM portal_orgs WHERE id = 10;
-- 10 | e2e  | failed_deprovisioning | (null)
```

`provisioning_status` got set to `failed_deprovisioning` (so part of `_mark_failed` ran), but `last_failure` stayed NULL — the jsonb-write failed and was caught by the broader `try` in `_mark_failed` (line 326), so the failure metadata is lost.

**Root cause:**
[`deprovisioning_orchestrator.py:329-332`](../../klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py) uses raw `text()` SQL to write the jsonb:
```python
await db.execute(
    text("UPDATE portal_orgs SET last_failure = :val WHERE id = :id"),
    {"val": failure_dict, "id": org_id},
)
```

asyncpg's prepared-statement binder expects a JSON-string for jsonb columns, not a Python dict. Classic pattern from `portal-backend.md::SQLAlchemy + RLS`:
> `::jsonb` casts conflict with SQLAlchemy `:param` — use `CAST(:param AS jsonb)` instead.

But cleaner: use the ORM (`org.last_failure = failure_dict`) — SQLAlchemy's PostgreSQL dialect knows how to map `dict` → `jsonb` natively.

**Cascade effect:**
Bug A is recoverable by an admin if `last_failure` is populated (admin reads it, decides on retry vs manual cleanup). With bug B, `last_failure` is NULL and the operator has to grep VictoriaLogs to find the actual error — slower diagnosis, no audit trail.

## Goal

Both bugs fixed so that:
1. Portal-api can DNS-resolve `meilisearch` (Bug A).
2. When any orchestrator step fails, `portal_orgs.last_failure` is populated with the failure metadata as a queryable jsonb (Bug B).
3. The e2e-tenant (org_id=10, slug=`e2e-37271947`) is fully deprovisioned via the orchestrator after the fix — verifying end-to-end correctness.

## Environment

- File `deploy/docker-compose.yml` change: networks-block for portal-api service
- File `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py` change: `_mark_failed` body
- File `klai-portal/backend/tests/test_deprovisioning_orchestrator.py` change: new RED-then-GREEN regression test
- Deploy via `deploy-compose.yml` GitHub workflow (compose change) — auto-syncs to core-01, force-recreates portal-api

## Out of scope

- Refactoring other raw-text jsonb writes elsewhere in the codebase — there may be similar latent bugs in other tables/services. Separate audit ticket.
- Adding a generic "portal-api network coverage" CI test (assert each deprovisioning_step's target hostname is in portal-api's network membership). Nice-to-have; future SPEC.
- Re-examining the provisioning-orchestrator for analogous network bugs. Provisioning worked yesterday on the `test` tenant — no evidence of a regression there.
- Fixing the unrelated `default_templates_seeding_failed` RLS bug — that's in flight via PR #657.
- Investigating why portal_users-INSERT did not land for org_id=10 during signup (separate finding, will surface again when Mark re-runs the signup flow after deprovisioning succeeds).

## Requirements

### REQ-1 — portal-api must reach Meilisearch on `klai-net-meilisearch`

**AC-1.1**: `deploy/docker-compose.yml` line ~535–540: portal-api's `networks:`-block includes `- net-meilisearch` (alongside the existing 5 networks).

**AC-1.2**: After deploy, `docker exec klai-core-portal-api-1 getent hosts meilisearch` returns an IP address.

**AC-1.3**: After deploy, `docker exec klai-core-portal-api-1 curl -s -o /dev/null -w '%{http_code}' http://meilisearch:7700/health` returns `200`.

### REQ-2 — `_mark_failed` writes failure metadata correctly

**AC-2.1**: `_mark_failed` uses the SQLAlchemy ORM to set `org.last_failure = failure_dict` (or equivalent `CAST(:val AS jsonb)` raw-SQL with `json.dumps(failure_dict)` as the bind value if the ORM path runs into a refresh-after-commit issue).

**AC-2.2**: A new regression test `tests/test_deprovisioning_orchestrator.py::test_mark_failed_writes_dict_as_jsonb` constructs a `_DeprovisionState`, calls `_mark_failed` with a known failure dict, and asserts that:
- `portal_orgs.last_failure` is the dict (not None)
- `portal_orgs.provisioning_status` is `failed_deprovisioning`
- No `DataError` raised

**AC-2.3**: The test fails RED against the current code (`asyncpg.exceptions.DataError`) and GREEN after the fix.

### REQ-3 — Real-world verification

**AC-3.1**: After both fixes deploy to production, `POST /api/admin/orgs/e2e-37271947/retry-deprovisioning` (or equivalent direct orchestrator invocation via `docker exec`) succeeds from `failed_deprovisioning` → `deprovisioned` state.

**AC-3.2**: After AC-3.1: `SELECT id FROM portal_orgs WHERE slug = 'e2e-37271947'` returns zero rows (hard-deleted by step 16 `_finalize_postgres_delete`).

**AC-3.3**: After AC-3.1: `docker ps --filter name=librechat-e2e` returns zero containers (step 3 cleanup).

**AC-3.4**: After AC-3.1: A query against the Zitadel admin API for user `e2e@getklai.com` returns 404 (step 14 `_delete_zitadel_org` cascade-deletes the org's users).

### REQ-4 — Quality gates

**AC-4.1**: `pytest tests/test_deprovisioning_orchestrator.py tests/services/provisioning/` green (full suite).

**AC-4.2**: `ruff check` + `ruff format --check` clean on changed files.

**AC-4.3**: `pyright` clean on changed files.

**AC-4.4**: CI workflows green: `portal-api` build + `Tenant Isolation Review` + `SAST — Semgrep` + `deploy-compose` workflow (auto-triggered by `deploy/docker-compose.yml` change).

## Migration plan

1. Worktree: `git worktree add ../klai-fix-deprovisioning -b fix/SPEC-INFRA-TENANT-DELETE-003 origin/main`
2. Write RED regression test for Bug B → confirm fails
3. Implement Bug B fix → confirm test GREEN
4. Implement Bug A fix (compose YAML edit)
5. Run wider regression suite (51+ tests for deprovisioning + provisioning + RLS)
6. Run ruff + pyright
7. Commit + push + PR (single PR for both bugs — same incident, same retro)
8. CI green → merge
9. `deploy-compose.yml` auto-fires → portal-api force-recreated with new network membership
10. Smoke-verify: `docker exec klai-core-portal-api-1 getent hosts meilisearch` returns an IP
11. Retry deprovisioning of org_id=10 via `docker exec` direct call (or admin retry-endpoint if I can get a session)
12. Verify AC-3.1 through AC-3.4 pass
13. Mark re-runs signup flow for `e2e@getklai.com` → reproduces (or absent) the portal_users-INSERT bug

## Rollback

- **Bug A rollback**: revert the one-line compose change. portal-api loses Meilisearch access; deprovisioning step 6 fails again. No other side-effects (portal-api had no code paths that required Meili access prior to step 6).
- **Bug B rollback**: revert the `_mark_failed` change. Failure metadata stops being written, but `provisioning_status` transition still works (so deprovisioning can still be retried, just without diagnostic context).

Combined rollback is `git revert <merge-sha>` followed by `deploy-compose.yml` workflow trigger.

## Risks

| Risk | Mitigation |
|---|---|
| `net-meilisearch` rename in compose breaks portal-api startup | Verified network exists and is used by other services (LibreChat, knowledge-ingest). Compose `docker compose config` validation catches typos pre-deploy. |
| Force-recreate portal-api drops in-flight requests | Standard for every portal-api deploy. Caddy has retry logic, frontend tolerates brief 502. |
| ORM `org.last_failure = dict` triggers `db.refresh()` semantics that don't work on a session in error state | Production check needed — but unlikely: portal_orgs is Cat-A-style RLS (not strict), and `_mark_failed` is called from a recovered try-block (not aborted-tx state). RED-test catches this if it fires. |
| Adding portal-api to `klai-net-meilisearch` expands SSRF surface | Negligible: portal-api already has full `klai-net` access (every internal service). Meili admin API was already reachable via tenant LibreChat-containers that portal-api creates. No security regression. |

## Tests

| Test | What it covers | New/existing |
|---|---|---|
| `test_mark_failed_writes_dict_as_jsonb` | Bug B fix — dict serializes correctly | new |
| `test_deprovisioning_orchestrator.py` (existing 11 tests) | Regression coverage | existing |
| `tests/services/provisioning/test_orchestrator.py` (existing 12 tests) | Provisioning still works after fix | existing |
| Manual `getent hosts meilisearch` post-deploy | Bug A fix verified on prod | manual |
| Manual deprovisioning of org_id=10 | End-to-end correctness | manual |

No new docker-compose-level test — the only meaningful verification is post-deploy DNS, and that's a one-liner Mark or I run after the merge.

## Acceptance summary

1. portal-api can reach Meilisearch (`getent hosts meilisearch` returns IP).
2. `_mark_failed` writes the failure dict to `portal_orgs.last_failure` as jsonb.
3. e2e tenant (org_id=10) is fully gone after retry: no portal_orgs row, no librechat-e2e container, no Zitadel `e2e@getklai.com` user.
4. CI green, no regressions in provisioning or deprovisioning test suite.
5. Mark can re-run signup for `e2e@getklai.com` and either (a) succeeds → tenant ready for testing, or (b) hits the portal_users-INSERT bug from earlier finding → separate SPEC.

## Out-of-band notes

This SPEC was authored during a single working session that began as "create the e2e test tenant" and ended up exposing:
- A templates-seeding RLS bug (fixed in PR #657)
- The Meilisearch-network bug (this SPEC)
- The jsonb-encode bug (this SPEC)
- A portal_users-INSERT-missing bug on org_id=10 (parked, will reproduce on next signup attempt)

The pattern is identical for all four: a flow that has unit tests covering the happy path, but no production end-to-end verification has ever run successfully. Each new tenant-lifecycle action surfaces one. The e2e test suite (SPEC-E2E-PROD-TENANT, in progress) exists specifically to eliminate this class — every flow gets a live production smoke test that runs on every deploy.
