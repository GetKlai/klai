# SPEC-TI-010A-FOLLOWUP-001 — scribe-api RLS infrastructure + transcribe.py cross-org leak

**Predecessor:** PR #382 (closed, branch `feature/SPEC-TI-010A-MARKERS-SCRIBE`); SPEC-TI-010A unmerged
**Pitfalls:** `rls-policy-shape-must-match-lifespan-assert` (HIGH), `asyncpg-pool-guc-not-shared` (HIGH), `multi-layer-gate-audit-all-sides` (HIGH)
**Priority:** HIGH
**Status:** Ready

## Goal

Make scribe-api safe to enable RLS on. Today `klai-scribe/scribe-api/app/core/database.py` is 13 lines — `create_async_engine` + `AsyncSessionLocal`, nothing else. There is no `set_tenant`, no `tenant_scoped_session`, no `cross_org_session`, no `_pin_and_reset_connection`. The original SPEC-TI-010A tried to add `org_id` + RLS policies to `scribe.transcriptions` without first landing this infrastructure, which would have produced a worst-of-both outcome: every INSERT blocked by the WITH CHECK clause (no GUC set → policy false), and every SELECT leaking cross-org via the policy's `USING (... IS NULL)` fail-open branch. Audit also caught a missing `org_id` filter in the KB-ingest endpoint that lets a multi-org Zitadel user pull another tenant's transcript into their own KB.

## Acceptance criteria

- **AC-1** `klai-scribe/scribe-api/app/core/database.py` exposes `set_tenant(db, org_id)`,
  `tenant_scoped_session(org_id)`, `cross_org_session()`, and `_pin_and_reset_connection`,
  copied verbatim from `klai-connector/app/core/database.py` and adapted for the scribe
  schema (GUC names, schema search_path). The full GUC lifecycle (set on entry, reset
  on exit, reset-on-pool-return) is preserved.

- **AC-2** Every authenticated scribe endpoint calls `set_tenant(db, caller.org_id)`
  immediately after auth resolution and BEFORE any query against `scribe.transcriptions`.
  Endpoints in scope: `POST /v1/transcribe`, `POST /v1/transcriptions/{id}/retry`,
  `POST /v1/transcriptions/{id}/ingest`, `GET /v1/transcriptions`,
  `GET /v1/transcriptions/{id}`, `PATCH /v1/transcriptions/{id}`,
  `DELETE /v1/transcriptions/{id}`.

- **AC-3** `ingest_transcription_to_kb` in `app/api/transcribe.py` adds
  `Transcription.org_id == caller.org_id` to the SELECT WHERE clause alongside the
  existing `user_id` filter. Regression test seeds two rows with the SAME `user_id`
  but different `org_id` against a real Postgres fixture and asserts that the ingest
  call from `org_a`'s context returns/processes only `org_a`'s row. Mocked-DB tests
  do NOT satisfy this AC — the existing `test_ingest_cross_org_denied` mocks `None`
  and so misses the leak in production.

- **AC-4** `app/services/reaper.py` switches from bare `AsyncSessionLocal()` to
  `cross_org_session()` and the docstring explicitly states the cross-org-by-design
  rationale. The reaper no longer relies on the policy's `USING IS NULL` branch as
  its safety net.

- **AC-5** New post-deploy SQL `post_deploy_<rev>_scribe_transcriptions_rls.sql`
  is idempotent (`DROP POLICY IF EXISTS` then `CREATE POLICY`) and applies the
  Cat-D strict shape on `scribe.transcriptions`: `USING (org_id = _rls_current_org_id())`
  and `WITH CHECK (org_id = _rls_current_org_id())`. No `IS NULL` fallback in either
  clause — every authenticated caller has an `org_id`, and the reaper now uses
  `cross_org_session()` (AC-4). See pitfall `rls-policy-shape-must-match-lifespan-assert`.

- **AC-6** Integration test against a real Postgres fixture asserts: (a) INSERT into
  `scribe.transcriptions` with no GUC set raises a policy violation, (b) INSERT with
  GUC set to a matching `org_id` succeeds, (c) SELECT with no GUC set returns 0 rows,
  (d) SELECT with GUC set to `org_a` returns only `org_a`'s rows when the table holds
  rows for `org_a` and `org_b`. All four cases must pass.

- **AC-7** Regression test for `klai-portal/backend/app/api/internal_connectors.py`
  asserts that `set_tenant(db, connector.org_id)` is called on the session BEFORE the
  subsequent DELETE statement. The C-5 cross-org-by-design pattern (initial SELECT
  spans all orgs to find the connector, then pin to its org for the delete) is
  preserved; the test locks it in.

- **AC-8** Depends on SPEC-TI-011-PER-SERVICE-DB-ROLES providing the `scribe_api`
  non-superuser role. RLS does not fire while scribe-api connects as `klai`
  (superuser bypasses RLS). Operator step below MUST run AFTER SPEC-TI-011 has
  migrated scribe-api to its dedicated role.

## Background

The PR #382 audit caught a worst-of-both failure mode that would have shipped if the
post-deploy SQL had been applied without the helper infrastructure:

```sql
-- The proposed policy from PR #382:
USING (org_id = NULLIF(current_setting('app.current_org', true), '')::int
       OR current_setting('app.current_org', true) = '')
WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::int);
```

With no `set_tenant` call ever happening, every connection sees `app.current_org = ''`:
- INSERT path: `WITH CHECK` evaluates `org_id = NULL`, which is `NULL` (falsy) → policy
  rejection on every write. Scribe-api stops accepting transcriptions.
- SELECT path: `USING` evaluates the second branch (`'' = ''` → true) → all rows visible
  to every connection regardless of caller. Cross-org leak on every read.

Writes blocked, reads leak. The fix is to land the helper infrastructure FIRST so
every authenticated query has a real GUC value, then apply a Cat-D strict policy with
no `IS NULL` escape hatch.

The transcribe.py leak is independent: a Zitadel user with memberships in `org_a` and
`org_b` (same `user_id` across both) can call `POST /v1/transcriptions/{id}/ingest`
from `org_a`'s portal session against `org_b`'s transcription ID. The current SELECT
filters only on `user_id`, so the row is returned and ingested into `org_a`'s KB.
All other transcribe.py endpoints already use the dual `user_id + org_id` filter —
ingest is the lone exception.

## Operator step (after merge + SPEC-TI-011)

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
    < klai-scribe/scribe-api/alembic/versions/post_deploy_<rev>_scribe_transcriptions_rls.sql
```

Verify zero policy-violation errors and zero cross-org SELECT anomalies in
VictoriaLogs over the next hour:
- `service:scribe-api AND message:"new row violates row-level security"`
- `service:scribe-api AND level:error AND message:"transcriptions"`

## Out of scope

- Refactor of scribe-api to SQLAlchemy 2.x style or asyncpg-direct patterns.
- Whisper-server, scribe ingest workers, or any scribe component beyond scribe-api.
- Migration of `scribe.transcriptions` to a different schema or table layout.
- Migrating scribe-api off the `klai` superuser DSN — separate SPEC: `SPEC-TI-011-PER-SERVICE-DB-ROLES`.
- Audit of other scribe schemas (`scribe.jobs`, etc.) for the same class of bug —
  separate SPEC if those tables hold tenant data.
