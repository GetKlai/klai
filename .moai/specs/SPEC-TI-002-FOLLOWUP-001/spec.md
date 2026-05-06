# SPEC-TI-002-FOLLOWUP-001 — Fix connector sync_engine + scheduler RLS wiring

**Predecessor:** `SPEC-TI-002-RLS-CONNECTOR` (PR #375, merged 2026-05-06)
**Adversarial audit:** PR #381 (`feature/SPEC-TI-010B-REDIS`, closed) — wiring gap found in connector background paths.
**Pitfall:** `asyncpg-pool-guc-not-shared` (HIGH) — same class, SQLAlchemy variant: a fresh session from `session_maker()` has no `app.current_org_id` GUC.
**Priority:** MEDIUM (latent on prod — `klai` superuser bypasses RLS today; activates the moment SPEC-TI-011 lands per-service roles)
**Status:** Ready

## Goal

Close the wiring gap left by SPEC-TI-002 in two connector background
modules. `sync_engine.py` and `scheduler.py` currently open SQLAlchemy
sessions via raw `async with self._session_maker() as session` blocks
that never go through `set_tenant`, `tenant_scoped_session`, or
`cross_org_session`. After SPEC-TI-002's `FORCE ROW LEVEL SECURITY` is
applied AND SPEC-TI-011 migrates klai-connector off the superuser DSN,
every background sync raises 42501. The reference correct pattern
already lives in `app/services/sync_run_reaper.py` and
`app/services/sync_run_resolver.py` — replicate it on the five
remaining sites.

## Acceptance criteria

- **AC-1** Every `async with self._session_maker() as session` in
  `klai-connector/app/services/sync_engine.py` is replaced with
  either `tenant_scoped_session(org_id)` (when org is known) or
  `cross_org_session()` (when the path deliberately spans orgs). Each
  replacement carries an inline comment documenting the choice.

- **AC-2** Every `async with session_maker() as session` /
  `async with db_session_maker() as session` in
  `klai-connector/app/services/scheduler.py` is replaced with the
  same helpers. The `start()` sweep over all connectors uses
  `cross_org_session()` (deliberate cross-org); `_trigger_sync` first
  resolves `Connector.org_id` and then opens
  `tenant_scoped_session(org_id)` for the `SyncRun` INSERT.

- **AC-3** `_execute_sync` (sync_engine.py:196) uses
  `tenant_scoped_session(portal_config.zitadel_org_id)`. The web
  crawler delegation branch (line 538) does the same.

- **AC-4** `_fail_sync_run` (sync_engine.py:725) is updated to either
  (a) accept `org_id` from its callers and use
  `tenant_scoped_session(org_id)`, OR (b) use `cross_org_session()`
  with a docstring explaining why a failed-run UPDATE may be applied
  cross-org (typical: failure path may run when portal_config lookup
  itself failed). Whichever option is chosen MUST be documented at the
  call site.

- **AC-5** A new regression test
  `klai-connector/tests/services/test_rls_wiring.py` mocks
  `session_maker()` to return a session whose underlying connection
  raises `InsufficientPrivilegeError` unless
  `app.current_org_id` is set on it. The test exercises every
  sync_engine + scheduler entry point that issues DML and asserts no
  42501 escapes.

- **AC-6** Existing `tests/services/test_sync_engine_*.py` are extended
  to assert the GUC is set BEFORE any DML on `connector.sync_runs` /
  `connector.connectors` runs. Existing green tests stay green.

- **AC-7** This SPEC depends on SPEC-TI-011 (per-service DB roles)
  landing before the wiring fix can be VERIFIED on prod. Until then,
  klai-superuser bypasses RLS and there is no observable failure to
  alert on.

## Background

Audit on origin/main found five offending call sites — all in the
connector's background sync path:

| File | Line | Function | Action |
|---|---|---|---|
| `app/services/sync_engine.py` | 196 | `_execute_sync` (`session.get(SyncRun, …)`) | `tenant_scoped_session(portal_config.zitadel_org_id)` |
| `app/services/sync_engine.py` | 538 | web crawler delegation path | `tenant_scoped_session(portal_config.zitadel_org_id)` |
| `app/services/sync_engine.py` | 725 | `_fail_sync_run` (UPDATE SyncRun) | `cross_org_session()` OR plumb org_id (AC-4) |
| `app/services/scheduler.py` | 45 | `start()` (SELECT all connectors) | `cross_org_session()` (deliberate cross-org sweep) |
| `app/services/scheduler.py` | 104 | `_trigger_sync` (INSERT SyncRun) | resolve org → `tenant_scoped_session(org_id)` |

The reference pattern already exists in this service:
`app/services/sync_run_reaper.py` and
`app/services/sync_run_resolver.py` use `cross_org_session()` and
`tenant_scoped_session()` correctly.

## Operator step (after merge + SPEC-TI-011)

```bash
# After SPEC-TI-011 migrates klai-connector to a non-superuser role,
# tail VictoriaLogs for one full sync cycle and verify zero 42501s:
#   service:klai-connector AND (level:error OR error_code:42501)
```

No DDL, no migration. Pure code refactor; revert is `git revert <sha>`.

## Out of scope

- Refactoring SQLAlchemy models or renaming `session_maker`.
- Knowledge-ingest pg_store wiring — separate SPEC:
  `SPEC-TI-003-FOLLOWUP-001`.
- Scribe `app/core/database.py` helper infra — separate SPEC:
  `SPEC-TI-010A-FOLLOWUP-001`.
- Migrating services off the `klai` superuser DSN — separate SPEC:
  `SPEC-TI-011-PER-SERVICE-DB-ROLES`.
- The B-2 / B-5 / B-9 / B-10 Redis fixes from PR #381 — separate SPEC:
  `SPEC-TI-010B-FOLLOWUP-001`.
