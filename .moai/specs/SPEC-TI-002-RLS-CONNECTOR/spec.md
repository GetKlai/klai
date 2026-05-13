# SPEC-TI-002 — RLS rollout op connector schema

**Audit ref:** `reports/audit-tenant-isolation-2026-05-05/report.md` finding **A-7**
**Standards ref:** `reports/audit-tenant-isolation-2026-05-05/standards.md` sections 1, 3, 8, 9
**Priority:** HIGH
**Status:** Ready (vervangt of bouwt op SPEC-SEC-CONNECTOR-RLS-001 in flight)

## Goal

Sluiten van de DB-laag tenant-isolatie op `connector.connectors` en `connector.sync_runs`. Vandaag = ZERO RLS, alleen app-laag-filters. Eén refactor verwijderd van leak.

## Acceptance criteria (EARS)

- **AC-1** WHILE pin op `connector.connectors`: ENABLE + FORCE ROW LEVEL SECURITY + Cat-D policy `tenant_isolation` met expliciete `WITH CHECK (org_id = _rls_current_org_id())`.
- **AC-2** WHILE pin op `connector.sync_runs`: idem.
- **AC-3** WHEN service draait, helper-function `_rls_current_org_id() RETURNS text` staat in connector schema (org_id is varchar/text in dit service).
- **AC-4** WHEN portal-api of klai-knowledge-mcp callt klai-connector endpoint: tenant context wordt expliciet gezet via `set_tenant(db, org_id)` of `tenant_scoped_session(org_id)` helper.
- **AC-5** WHEN lifespan startup reset draait: `cross_org_session()` of expliciete `# cross-org-by-design:` comment + `app.cross_org_admin=true` GUC.
- **AC-6** WHEN `SyncRunReaper.tick()` draait: idem.
- **AC-7** Test: een query op `sync_runs` zonder tenant-context raise't `42501` insufficient_privilege.
- **AC-8** Test: cross-org-bypass via `cross_org_session()` werkt en retourneert ALLE rows.

## Implementation

1. Helper function in `klai-connector/alembic/versions/post_deploy_<rev>.sql` (apply als `klai` superuser).
2. Sessie-helpers gekopieerd naar `klai-connector/app/core/database.py` (asyncpg of SQLAlchemy variant — match bestaande style).
3. `routes/sync.py`, `routes/internal.py`: vervang sessie-acquisities met `tenant_scoped_session(org_id)` waar tenant bekend is.
4. `app/main.py` lifespan: wrap UPDATE in `cross_org_session()` met inline `# cross-org-by-design:` reden + SPEC-ref.
5. `services/sync_run_reaper.py::tick()`: idem.
6. Pin `sync_require_org_id=True` (al prod default).
7. `entrypoint.sh` heeft al `alembic upgrade head` (bevestigd).

## Tests

- `tests/test_connector_rls.py` (nieuw):
  - `test_rls_blocks_no_context()` — fail-loud op missing GUC
  - `test_rls_filters_by_org()` — twee orgs, alleen eigen rows
  - `test_cross_org_session_bypasses_rls()` — explicit bypass werkt
  - `test_with_check_blocks_foreign_org_insert()` — INSERT met andere org_id wordt geweigerd
- Bestaande `tests/test_sync_*.py` moeten groen blijven (regression).

## Operator-step (post-deploy)

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-connector/alembic/versions/post_deploy_<rev>.sql
docker restart klai-core-klai-connector-1
```

## Worktree

`klai-connector-rls` (bestaat al — `feature/SPEC-SEC-CONNECTOR-RLS-001`). Of nieuwe: `feature/SPEC-TI-002-RLS-CONNECTOR`.
