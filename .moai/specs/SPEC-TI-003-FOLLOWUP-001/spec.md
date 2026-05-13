# SPEC-TI-003-FOLLOWUP-001 — Refactor knowledge-ingest pg_store to pass `conn` explicitly

**Predecessor:** `SPEC-TI-003-RLS-KNOWLEDGE` (PR #376, merged 2026-05-06)
**Pitfall:** `asyncpg-pool-guc-not-shared` (HIGH)
**Priority:** MEDIUM (mitigated by emergency `NO FORCE`; activates when SPEC-TI-011 lands per-service roles)
**Status:** Ready

## Goal

Restore the tenant-isolation hardening promised by SPEC-TI-003 by making
the connection-locality of the RLS GUC explicit in the API. Today
`tenant_scoped_connection(org_id)` pins one connection while every
function in `pg_store.py` (and friends) grabs a different connection from
the pool, so the GUC is never visible to the queries that actually need
it. Audit on origin/main found 26 unique functions across 6 files
affected.

The bug is currently latent on prod because `klai` (the connecting role)
is a Postgres superuser and bypasses RLS regardless. SPEC-TI-011 will
move services to non-superuser roles; this SPEC is its prerequisite.

## Acceptance criteria

- **AC-1** Every function in `klai-knowledge-ingest/knowledge_ingest/`
  that issues SQL against `knowledge.*` tables takes an
  `asyncpg.Connection` (typically named `conn`) as its first non-`self`
  argument and uses it for all query execution.

- **AC-2** No function under `knowledge_ingest/` outside of `db.py`
  calls `get_pool()` or `pool.acquire()` while issuing SQL against
  `knowledge.*`. Allowed exceptions: `rag_eval_results` queries,
  procrastinate-internal calls, and lifespan-startup hooks (which use
  `cross_org_admin_connection()` — see AC-3).

- **AC-3** New helper `cross_org_admin_connection()` in `db.py` for
  startup reapers and deprovision sweeps. Mirrors
  `klai-connector/app/core/database.py::cross_org_session()`. Sets
  `app.cross_org_admin = 'true'` on the pinned connection.

- **AC-4** `tenant_scoped_connection(org_id)` docstring is updated to
  state explicitly that the GUC applies ONLY to queries via the
  yielded connection — pass it down, do not rely on pinning.

- **AC-5** A regression test in `tests/test_db_rls_wiring.py` runs
  two concurrent `tenant_scoped_connection(org_a)` and
  `tenant_scoped_connection(org_b)` blocks against a real Postgres
  fixture and asserts each block reads its own GUC (no bleed).

- **AC-6** A second regression test calls a refactored pg_store
  function WITHOUT a `conn` argument and expects a `TypeError` (the
  function signature now requires it). pyright/mypy must also flag
  the missing argument as a type error.

- **AC-7** New post-deploy SQL `post_deploy_dd1b439a57d0_force.sql`
  re-applies `FORCE ROW LEVEL SECURITY` on all 13 `knowledge.*` tables.
  Operator runs this AFTER (a) this SPEC's code is deployed and (b)
  SPEC-TI-011 has migrated knowledge-ingest off the klai-superuser DSN.

- **AC-8** The hot-fix `derivations` policy (currently scoped by
  `child_id`, hot-applied 2026-05-06) is back-filled into source as
  part of this SPEC.

- **AC-9** `_rls_current_org_id()` keeps its fail-loud `RAISE 42501`
  on missing GUC. Do NOT downgrade to `RETURN NULL` — that hides
  wiring gaps as silent default-deny.

## Background

Caller pattern that was wrong (verbatim from `crawl_tasks.py`):

```python
async with tenant_scoped_connection(org_id) as _conn:
    del _conn  # connection held open to keep GUC set; pg_store uses pool
    await run_crawl_job(...)  # ← grabs DIFFERENT pool conn → no GUC
```

Correct pattern:

```python
async with tenant_scoped_connection(org_id) as conn:
    await run_crawl_job(conn, ...)  # ← same conn → same GUC
```

For the full incident, see pitfall `asyncpg-pool-guc-not-shared` (HIGH).

## Operator step (after merge + SPEC-TI-011)

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
    < klai-knowledge-ingest/alembic/versions/post_deploy_dd1b439a57d0_force.sql
```

Verify zero 42501 errors in VictoriaLogs over the next hour.

## Out of scope

- Refactor of connector `sync_engine.py` / `scheduler.py` — same class
  of bug, separate SPEC: `SPEC-TI-002-FOLLOWUP-001`.
- Refactor of scribe `app/core/database.py` to add the helper
  infrastructure — separate SPEC: `SPEC-TI-010A-FOLLOWUP-001`.
- Migrating services off the `klai` superuser DSN — separate SPEC:
  `SPEC-TI-011-PER-SERVICE-DB-ROLES`.
- Migration of knowledge-ingest from asyncpg to SQLAlchemy.
