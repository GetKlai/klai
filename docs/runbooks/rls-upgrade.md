# RLS strict-mode upgrade runbook

PostgreSQL Row-Level Security policies on the portal database have two
modes:

- **Legacy (NULLIF pattern)**: `USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)` — silently filters rows
  to zero when tenant context is missing.
- **Strict (`_rls_current_org_id()` function)**: raises 42501
  `insufficient_privilege` when neither `app.current_org_id` nor
  `app.cross_org_admin='true'` is set.

Production runs strict mode as of 2026-04-21 for 11 category-D (pure-
tenant) tables. This runbook documents how to apply, verify, and roll
back.

## Files

| File | Purpose |
|---|---|
| `klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql` | Forward migration |
| `klai-portal/backend/alembic/versions/post_deploy_rls_rollback_to_nullif_pattern.sql` | Emergency rollback |
| `klai-portal/backend/scripts/rls-smoke-test.sh` | Post-apply verification |
| `klai-portal/backend/app/core/database.py` | `tenant_scoped_session`, `cross_org_session`, `pin_session`, `set_tenant` |
| `klai-portal/backend/app/core/rls_guard.py` | `after_cursor_execute` listener detecting rowcount=0 DML on RLS tables |

## Category framework

Every RLS-enabled table falls in one of four categories. The table's
category determines which policy shape applies. **Do not migrate a table
out of its category without reading this entire runbook.**

| Category | Policy shape | Tables |
|---|---|---|
| A — Auth-seed | `USING (org_id = … OR current_setting IS NULL)` — permissive on missing context | `portal_users`, `portal_connectors` |
| B — Public SELECT, strict writes | `FOR SELECT USING (true)`, other cmds strict | `widgets`, `widget_kb_access`, `partner_api_keys`, `partner_api_key_kb_access` |
| C — Permissive INSERT, strict read | `FOR INSERT WITH CHECK (true)` (or scoped), `FOR SELECT` scoped | `portal_audit_log`, `product_events`, `portal_feedback_events` |
| D — Pure tenant | `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())` — strict, with cross-org bypass | `portal_knowledge_bases`, `portal_groups`, `portal_group_products`, `portal_group_kb_access`, `portal_kb_tombstones`, `portal_user_kb_access`, `portal_user_products`, `portal_retrieval_gaps`, `portal_taxonomy_nodes`, `portal_taxonomy_proposals`, `vexa_meetings` |

When adding a new RLS-enabled table, decide its category first:

1. Is there a request path that queries the table **before** set_tenant
   can fire? (auth lookup, pre-auth webhook) → **A**.
2. Does a public/pre-auth endpoint SELECT from it? → **B**.
3. Is it fire-and-forget INSERT that must survive caller rollback? → **C**.
4. Otherwise → **D** — and audit every callsite before applying strict
   policy (see "Pre-flight" below).

## Pre-flight before expanding category D

Before migrating a new table to strict mode:

1. **Grep all queries** on the table's model:
   ```
   grep -rn "select(<ModelName>\|<tablename>" klai-portal/backend/app/
   ```
2. **For each call-site**, verify tenant context is set via one of:
   - Request came through `_get_caller_org` / `get_partner_key` / admin
     dependency chain (look for `Depends(…)` in the route).
   - Explicit `await set_tenant(db, org_id)` earlier in the function.
   - Session opened via `tenant_scoped_session(org_id)` or
     `cross_org_session()`.
3. **Any unprotected site** must either move to one of those patterns, or
   document a reason the table stays in category A. The RLS guard event
   listener will flag silent-filter at runtime; pre-flight catches it
   before the migration.
4. **Local strict-mode test**: run pytest with
   `PORTAL_RLS_GUARD_STRICT=1` — this raises on any rowcount=0 DML
   against an RLS table during tests.

## Apply forward migration

**Order matters. Deploy Python code first.** The strict policy refers to
`_rls_current_org_id()`, which the application code must set via
`tenant_scoped_session` / `set_tenant` / `cross_org_session` before its
queries hit RLS tables. Running SQL before code breaks every request
that still relies on the NULLIF fallback.

1. Deploy portal-api with the helpers (current main already contains
   them; verify with `docker inspect <portal-api> --format '{{.Image}}'`
   matches the latest `ghcr.io/getklai/portal-api:latest` digest).
2. Copy the script to the postgres host:
   ```
   scp klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql core-01:/tmp/rls_upgrade.sql
   ```
3. Apply as `klai` superuser:
   ```
   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai -v ON_ERROR_STOP=1 < /tmp/rls_upgrade.sql"
   ```
   Expected last lines:
   ```
   status
   ---------------------------------------------------------------------------
    RLS upgrade applied — _rls_current_org_id function and policies refreshed
   COMMIT
   ```
4. **If the script fails partway**: the entire script runs in one
   transaction (`BEGIN`/`COMMIT`), so ON_ERROR_STOP aborts and nothing is
   committed. Re-run after fixing the underlying schema mismatch; the
   DROP POLICY IF EXISTS clauses make it idempotent.

## Verify

Run the smoke-test script:

```
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U portal_api -d klai" \
    < klai-portal/backend/scripts/rls-smoke-test.sql
```

Expected outcome (see the script for the full set):

- `SELECT FROM portal_knowledge_bases` without tenant → **ERROR 42501**
  (this is the desired strict behaviour — not a bug).
- `SELECT FROM portal_users` without tenant → succeeds with zero rows
  (auth-seed pattern preserved).
- `SELECT` with `set_config('app.current_org_id', '<id>', false)` → returns
  rows for that tenant.
- `SELECT` with `set_config('app.cross_org_admin', 'true', false)` →
  returns all rows across tenants.

In VictoriaLogs, watch for any `service:portal-api AND
("insufficient_privilege" OR "42501" OR "RLS silent-filter")` entries
post-migration. Any hit means a code path is missing tenant context.

## Roll back

Use when a code regression is making strict policies block legitimate
traffic and you cannot quickly ship a fix. **First** try rolling forward
by deploying a fixed portal-api. Only rollback the SQL if that is not
feasible.

1. Copy the rollback script:
   ```
   scp klai-portal/backend/alembic/versions/post_deploy_rls_rollback_to_nullif_pattern.sql core-01:/tmp/rls_rollback.sql
   ```
2. Apply as `klai` superuser:
   ```
   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai -v ON_ERROR_STOP=1 < /tmp/rls_rollback.sql"
   ```
   Expected last line: `RLS rollback complete — policies restored to NULLIF pattern, _rls_current_org_id() dropped`.
3. The rollback drops `_rls_current_org_id()` along with restoring the
   policies — this is safe because no policy or function references it
   after the rollback. `cross_org_session()` in Python still runs; its
   `set_config('app.cross_org_admin', 'true', false)` becomes a harmless
   no-op (no policy reads that setting after rollback).

## Reapplying after rollback

Reapplying the forward migration is safe — all `DROP POLICY IF EXISTS`
and `CREATE OR REPLACE FUNCTION` statements are idempotent.

## Anti-patterns to watch for in code review

- `async with AsyncSessionLocal() as db:` followed by a query on a
  category-D table (should be `tenant_scoped_session(org_id)` unless
  genuinely cross-org).
- A new function that takes `db: AsyncSession` as parameter and queries
  a category-D table **without** also requiring `org_id` as parameter
  (the caller may have forgotten to call `set_tenant`).
- FastAPI endpoint with no `_get_caller_org` / `get_partner_key`
  dependency that touches a category-D table — check whether
  `set_tenant` runs elsewhere in the request flow.
- ORM lazy-load of a relationship that crosses RLS boundaries (e.g.
  `org.knowledge_bases` triggers a fresh query — that query runs under
  whatever tenant context the session currently has).
