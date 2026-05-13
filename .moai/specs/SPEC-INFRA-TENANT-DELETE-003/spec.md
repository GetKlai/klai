---
id: SPEC-INFRA-TENANT-DELETE-003
version: "0.6.0"
status: completed
created: "2026-05-13"
updated: "2026-05-13"
author: Mark Vletter
priority: high
related:
  - SPEC-INFRA-TENANT-DELETE-001 (16-step orchestrator — DEPLOYED, broken in prod)
  - SPEC-INFRA-TENANT-DELETE-002 (G3 + G6 wipe-via-internal-endpoint siblings — DEPLOYED)
  - SPEC-INFRA-CONFIG-SYNC-001 (bind-mount-config-sync auto-rsync — DEPLOYED)
discovered_during: SPEC-E2E-PROD-TENANT preparation (this session, 2026-05-13)
merge_commits:
  - cfb03d46 (Bug A + B — initial SPEC, jsonb + Meili net)
  - 8e7f9c71 (Bug C — garage endpoint scheme)
  - 8b5317fc (Bug D — NoSuchBucket idempotent)
  - e9a15591 (Bug E — Zitadel RemoveOrg endpoint)
  - 4a14e1f2 (Bug F/G/H/I — finalize FK list drift)
---

# SPEC-INFRA-TENANT-DELETE-003: Fix deprovisioning — nine never-tested-on-prod bugs

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-13 | Mark Vletter | Initial. Two bugs found while attempting first real-world tenant deprovisioning. Both bugs make `SPEC-INFRA-TENANT-DELETE-001` orchestrator unusable for production tenants when invoked from portal-api. |
| 0.2.0 | 2026-05-13 | Mark Vletter | Bug C added — `_delete_scribe_artifacts` boto3 client rejected schemeless `garage:3900` from `GARAGE_S3_ENDPOINT`. Surfaced on first retry after Bug A+B shipped. Fix: defensive `http://` prepend in `_delete_scribe_artifacts` so the same env var continues to work for the canonical Minio reader. Merged in `8e7f9c71`. |
| 0.3.0 | 2026-05-13 | Mark Vletter | Bug D added — `_delete_scribe_artifacts` raised `NoSuchBucket` on tenants whose Scribe bucket was never lazy-created (no audio uploads ever happened). Contradicts SPEC R3 "al-weg = geen exception". Fix: catch `NoSuchBucket` in `_sync_delete`, log `scribe_artifacts_bucket_absent`, return cleanly. Merged in `8b5317fc`. |
| 0.4.0 | 2026-05-13 | Mark Vletter | Bug E added — `zitadel.delete_org` POSTed to `DELETE /management/v1/orgs` (no `/me`), which is the CreateOrg endpoint and returned 405. Verified the correct path is `DELETE /management/v1/orgs/me` with `x-zitadel-orgid` header. Fix: correct URL + accept 403 as idempotent-skip (matches "no grant on deleted org" semantics). Merged in `e9a15591`. |
| 0.5.0 | 2026-05-13 | Mark Vletter | Bug F/G/H/I added — `_finalize_postgres_delete` explicit-DELETE list had drifted from the schema. Bug F: obsolete `portal_products` (dropped by `rbac001_drop_legacy_rbac_data`) → UndefinedTableError aborts the whole tx before reaching anything else. Bug G/H/I: missing `portal_group_products`, `portal_user_products` (RBAC-001) and `portal_user_seat_history` (PRICING-PER-USER-001) — non-cascading FKs that would block portal_orgs DELETE if Bug F were the only fix. Audit via `pg_constraint` on prod. Merged in `4a14e1f2`. |
| 0.6.0 | 2026-05-13 | Mark Vletter | Sync. Status → completed after end-to-end verification on prod: org_id=10 hard-deleted (REQ-3.1+3.2), no `librechat-e2e*` containers (REQ-3.3), original Zitadel `e2e@getklai.com` user gone (REQ-3.4; the current `e2e@getklai.com` user is a fresh signup from 16:47 UTC under a different resourceOwner — exactly the re-signup path of Acceptance 5(a)). |

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

### Bug C — `_delete_scribe_artifacts` boto3 client rejects schemeless endpoint (HIGH)

**Symptom (surfaced on first retry after Bug A+B shipped):**
```
ValueError: Invalid endpoint: garage:3900
```

**Root cause:**
`_delete_scribe_artifacts` constructs a boto3 S3 client with `endpoint_url=settings.garage_s3_endpoint`. The production env value is the schemeless `garage:3900` because the canonical reader (`kb_images.py::_make_minio_client`) uses the Minio SDK which takes `host:port` + `secure=False`. boto3 wants an `http(s)://` URL or it refuses to construct the client.

**Fix:** Defensive `http://` prepend when the endpoint has no scheme, so the same env var works for both consumers without forcing operators to track two variants. Merged in `8e7f9c71`. Regression test `test_schemeless_endpoint_gets_http_scheme_prepended` locks the boto3 bind shape.

### Bug D — `_delete_scribe_artifacts` not idempotent on missing bucket (HIGH)

**Symptom (after Bug C unblocked client construction):**
```
botocore.errorfactory.NoSuchBucket: An error occurred (NoSuchBucket)
when calling the ListObjectsV2 operation: Bucket not found: klai-scribe
```

**Root cause:**
The Scribe S3 backend isn't deployed in production, or the bucket hasn't been auto-created yet (Scribe lazily creates it on first audio upload, which this e2e tenant never did). The step treated a missing bucket as a hard failure, contradicting SPEC R3 ("al-weg = geen exception").

**Fix:** Catch `NoSuchBucket` inside `_sync_delete`, log `scribe_artifacts_bucket_absent`, return cleanly. Same semantic as the existing "no objects found" path. Merged in `8b5317fc`. Regression test `test_no_such_bucket_is_idempotent` simulates the boto3 raise and asserts the step does not propagate.

### Bug E — `zitadel.delete_org` POSTs to CreateOrg endpoint, returns 405 (HIGH)

**Symptom (after Bug D unblocked step 8):**
```
HTTP 405 Method Not Allowed from DELETE /management/v1/orgs
```

**Root cause:**
`zitadel.delete_org` called `DELETE /management/v1/orgs` (no `/me`). That URL is the CreateOrg endpoint, which is POST-only — DELETE returns 405. Verified by hand-curling `DELETE /management/v1/orgs/me` with `x-zitadel-orgid: <org-id>` against production Zitadel — returned 200.

**Fix:**
- URL becomes `/management/v1/orgs/me`. The `x-zitadel-orgid` header continues to scope which org "me" resolves to.
- 403 added as idempotent-skip status alongside 404. Zitadel sometimes returns 403 when the calling identity no longer has any grant on a deleted org — exactly the "already gone" semantic SPEC R3 needs.

Merged in `e9a15591`. Tests updated: `_DELETE_PATH` constant + `test_403_is_idempotent_returns_none` (flipped from the prior raise expectation).

### Bug F/G/H/I — `_finalize_postgres_delete` FK list drifted from schema (HIGH)

**Symptom (after Bug E unblocked step 14, on step 16):**
```
psycopg.errors.UndefinedTable: relation "portal_products" does not exist
```

…and would have been followed by FK violations on portal_orgs DELETE if Bug F were the only fix.

**Root cause:**
The explicit-DELETE list in `_finalize_postgres_delete` was authored when SPEC-INFRA-TENANT-DELETE-001 landed and hadn't been audited against subsequent schema changes. `pg_constraint` audit on prod 2026-05-13:

- **Bug F (obsolete in DELETE list):** `portal_products` — dropped by `rbac001_drop_legacy_rbac_data`. UndefinedTableError aborts the whole transaction before touching anything else.
- **Bug G/H/I (missing from DELETE list):**
  - `portal_group_products` (RBAC-001, no-cascade FK)
  - `portal_user_products` (RBAC-001, no-cascade FK)
  - `portal_user_seat_history` (PRICING-PER-USER-001, no-cascade FK)

  Each would FK-violate the `portal_orgs` hard-delete if Bug F were resolved alone.

**Fix:** Update the explicit-DELETE list to match production schema. Order: KB tables → vexa_meetings → group_products → groups → templates → user_products → user_seat_history → users → portal_orgs. Merged in `4a14e1f2`. Test `test_execute_called_for_each_non_cascading_child_table` extended from 9 to 11 calls; docstring references the RBAC-001 + PRICING-PER-USER-001 lineage so the next schema change is flagged.

**Class summary (Bugs A-I):**
Every bug here is the same shape: a deprovisioning orchestrator step that worked in unit tests but had never been exercised end-to-end on a real production tenant. Each fix unblocked the retry to the *next* never-tested step, which then crashed in its own way. The SPEC's "Out-of-band notes" predicted exactly this — *"Each new tenant-lifecycle action surfaces one"*. SPEC-E2E-PROD-TENANT is the structural remediation: every deprovisioning step gets a live production smoke-test that runs on every deploy.

## Goal

All nine bugs fixed so that:
1. Portal-api can DNS-resolve `meilisearch` (Bug A).
2. When any orchestrator step fails, `portal_orgs.last_failure` is populated with the failure metadata as a queryable jsonb (Bug B).
3. `_delete_scribe_artifacts` constructs a valid boto3 S3 client against the production `GARAGE_S3_ENDPOINT` value (Bug C).
4. `_delete_scribe_artifacts` treats a missing Scribe bucket as idempotent-skip (Bug D).
5. `zitadel.delete_org` targets the correct Management API endpoint and treats 403 as idempotent (Bug E).
6. `_finalize_postgres_delete` explicit-DELETE list matches the current production schema — no obsolete table references, all non-cascading FK children covered (Bug F/G/H/I).
7. The e2e-tenant (org_id=10, slug=`e2e-37271947`) is fully deprovisioned via the orchestrator after all fixes — verifying end-to-end correctness.

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
| `test_mark_failed_writes_dict_as_jsonb` | Bug B fix — dict serializes correctly via CAST(:val AS jsonb) | new |
| `test_schemeless_endpoint_gets_http_scheme_prepended` | Bug C fix — boto3 client constructed with scheme | new |
| `test_no_such_bucket_is_idempotent` | Bug D fix — `NoSuchBucket` swallowed, no delete_objects call | new |
| `test_zitadel_delete_org.py::_DELETE_PATH` | Bug E fix — DELETE hits `/management/v1/orgs/me` | updated |
| `test_zitadel_delete_org.py::test_403_is_idempotent_returns_none` | Bug E fix — 403 treated as already-gone | flipped |
| `test_execute_called_for_each_non_cascading_child_table` | Bug F/G/H/I fix — explicit-DELETE list matches schema (11 calls, ordered) | extended |
| `test_deprovisioning_orchestrator.py` (existing 18 tests) | Regression coverage | existing |
| `tests/services/provisioning/test_orchestrator.py` (existing 12 tests) | Provisioning still works after fix | existing |
| Manual `getent hosts meilisearch` post-deploy | Bug A fix verified on prod | done — `172.21.0.2` |
| Manual `python urlopen meilisearch:7700/health` post-deploy | Bug A fix verified on prod | done — `200` |
| Manual deprovisioning of org_id=10 | End-to-end correctness (all 9 bugs) | done — row gone, container gone, original Zitadel user gone |

No new docker-compose-level test — the only meaningful verification is post-deploy DNS, and that's a one-liner Mark or I run after the merge.

## Acceptance summary

1. ✅ portal-api can reach Meilisearch (`getent hosts meilisearch` → `172.21.0.2`; HTTP `/health` → 200, verified 2026-05-13 17:59 UTC).
2. ✅ `_mark_failed` writes the failure dict to `portal_orgs.last_failure` as jsonb (regression test + production retry both confirm).
3. ✅ e2e tenant (org_id=10) fully gone after retry:
   - `SELECT id FROM portal_orgs WHERE id = 10` → 0 rows.
   - `docker ps -a --filter name=librechat-e2e` → 0 containers.
   - Zitadel `e2e@getklai.com` under the deprovisioned org → gone. (A fresh `e2e@getklai.com` exists in a different resourceOwner from the post-cleanup signup attempt at 16:47 UTC — that is the Acceptance 5(a) success-case below, not a leftover.)
4. ✅ CI green, no regressions in provisioning or deprovisioning test suite (74/74 deprovisioning + orchestrator + zitadel tests).
5. ✅ (a) Mark re-ran signup for `e2e@getklai.com` — succeeded (USER_STATE_INITIAL, fresh resourceOwner 362757920133283846).

## Out-of-band notes

This SPEC was authored during a single working session that began as "create the e2e test tenant" and ended up exposing **nine** never-tested-on-prod bugs in the same orchestrator:
- A templates-seeding RLS bug (fixed in PR #657 — separate SPEC)
- Bug A — Meilisearch network (this SPEC, merge `cfb03d46`)
- Bug B — jsonb encode (this SPEC, merge `cfb03d46`)
- Bug C — garage endpoint scheme (this SPEC, merge `8e7f9c71`)
- Bug D — NoSuchBucket idempotent (this SPEC, merge `8b5317fc`)
- Bug E — Zitadel RemoveOrg endpoint (this SPEC, merge `e9a15591`)
- Bug F/G/H/I — FK list drift (this SPEC, merge `4a14e1f2`)
- A portal_users-INSERT-missing bug on org_id=10 (resolved during re-signup at 16:47 UTC — fresh user landed cleanly)

The pattern is identical: a flow that has unit tests covering the happy path, but no production end-to-end verification has ever run successfully. Each new tenant-lifecycle action surfaces one. The e2e test suite (SPEC-E2E-PROD-TENANT, in progress) exists specifically to eliminate this class — every flow gets a live production smoke test that runs on every deploy.

This SPEC is the canonical worked example of why SPEC-E2E-PROD-TENANT matters: nine bugs that all passed unit tests, all caught by a single end-to-end run, all fixed within hours of being surfaced. A pre-deploy e2e smoke would have caught Bugs A through I in one CI run before any of them shipped to production.
