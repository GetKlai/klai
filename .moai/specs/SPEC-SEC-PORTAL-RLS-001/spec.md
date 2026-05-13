---
id: SPEC-SEC-PORTAL-RLS-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster C)
---

# SPEC-SEC-PORTAL-RLS-001: RLS coverage voor `portal_join_requests` + `portal_org_allowed_domains`

## Summary

Voeg RLS-policies toe aan twee portal-tabellen die wel `org_id` kolom hebben maar geen `CREATE POLICY` (pre-auth invitation flows). Plus mechanische guards via ast-grep om untenanted SyncRun queries in klai-connector te blokkeren.

## Motivation

Per `reports/audit-2026-05-04/tenant-scoping.md`:
- TP-1 HIGH: `portal_join_requests` mist RLS — admin token-based approve heeft geen DB-laag fallback
- TP-2 HIGH: `portal_org_allowed_domains` mist RLS — domain-claims kunnen cross-tenant leaken
- TP-5 HIGH-onbekend: `klai-connector/connector.sync_runs` heeft `org_id` maar geen `CREATE POLICY`

## Scope

### In scope

1. **`portal_join_requests`** Category-D RLS-policy met `OR T IS NULL` branch voor pre-auth `auth_select.py` flow
2. **`portal_org_allowed_domains`** Category-D RLS-policy
3. Beide tabellen toevoegen aan `RLS_DML_TABLES` in `app/core/rls_guard.py`
4. ast-grep rule `rules/no-untenanted-syncrun-query.yml` die elke `select(SyncRun)`/`update(SyncRun)`/`delete(SyncRun)` zonder `.where(SyncRun.org_id == ...)` flagt als CI violation
5. Test `klai-connector/tests/test_sync_runs_tenant_isolation.py` met twee tenants
6. Regression-test in portal `tests/test_rls_coverage.py` die `pg_policies` checkt op alle tabellen met `org_id`

### Out of scope

- Postgres RLS op `connector.sync_runs` zelf (te zwaar voor deze SPEC; pure ast-grep + tests volstaan)
- RLS op andere niet-portal services (knowledge.*, scribe.*, research.*) — apart per service

## Acceptance criteria

1. Alembic migration + post-deploy SQL voor beide tabellen
2. Migration draait op staging zonder errors
3. `assert_portal_users_rls_ready` startup-assertion uitgebreid voor de twee nieuwe tabellen
4. ast-grep rule fired in CI op test-fixture met untenanted SyncRun query
5. Regression-test detecteert ontbrekende policy als RLS_DML_TABLES uit sync raakt met `pg_policies`

## Implementation outline

```sql
-- alembic migration + post-deploy SQL
ALTER TABLE portal_join_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_join_requests FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON portal_join_requests
  USING (org_id = current_setting('app.current_org_id', true)::int
         OR current_setting('app.current_org_id', true) IS NULL)
  WITH CHECK (org_id = current_setting('app.current_org_id', true)::int);

-- Idem voor portal_org_allowed_domains, maar Category-D strict (no OR NULL)
```

## Risks

| Risk | Mitigatie |
|---|---|
| `auth_select.py` flow breekt door strict RLS | Category-D `OR T IS NULL` branch voor pre-auth |
| Admin token-based approve auth_join verbreken | Test `test_auth_join_token_approve_after_rls` |
| ast-grep false-positives op test-fixtures | `paths_exclude_glob` voor `tests/regression-fixtures/` |

## References

- `reports/audit-2026-05-04/tenant-scoping.md` (TP-1, TP-2, TP-5)
- `.claude/rules/klai/projects/portal-security.md` (4-categorieën RLS framework)
- `klai-portal/backend/app/core/rls_guard.py::RLS_DML_TABLES`
