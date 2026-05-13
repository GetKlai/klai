# SPEC-TI-005 — portal-api RLS hygiëne-batch

**Audit ref:** findings **A-1, A-2, A-3, A-4, A-5, A-6**
**Standards ref:** `standards.md` sections 1, 2, 8
**Priority:** HIGH (samengesteld uit MED-findings — combinatie levert defense-in-depth-fundamenten)
**Status:** Ready

## Goal

Eén post-deploy SQL die alle 6 portal-api RLS hygiëne-gaten dichttrekt + bijhorende code-asserties.

## Acceptance criteria (EARS)

- **AC-1 (A-1)** Post-deploy ALTER POLICY op `portal_users` + `portal_connectors`: expliciete `WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int)` (zonder OR-NULL branch in WITH CHECK). USING blijft Cat-A permissive op IS NULL.
- **AC-2 (A-2)** Nieuwe policy op `portal_group_memberships`: subquery-pattern via parent group.
- **AC-3 (A-3)** ENABLE + FORCE op `partner_api_keys` + `partner_api_key_kb_access` (vandaag alleen in docstring) + nieuwe startup-assertion `assert_partner_api_keys_rls_ready()` in `app/core/database.py` analoog aan `assert_portal_users_rls_ready()`.
- **AC-4 (A-4)** ALTER TABLE FORCE op `portal_feedback_events`, `widgets`, `widget_kb_access`, `tenant_lifecycle_events` (vandaag alleen ENABLE).
- **AC-5 (A-5)** Vervang `WITH CHECK (true)` op INSERT-policies van `portal_audit_log`, `product_events`, `portal_feedback_events`, `tenant_lifecycle_events` met `current_setting('app.current_org_id', true) = '' OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::int` (Cat-C verbeterd).
- **AC-6 (A-6)** `tenant_lifecycle_events` SELECT-policy GUC-pattern gedocumenteerd in code + audit-log-event-emission helper-comment.

## Implementation

Eén post-deploy SQL: `klai-portal/backend/alembic/versions/post_deploy_<rev>_tenant_isolation_hygiene.sql`. Wrap in `BEGIN/COMMIT`. Idempotent via `IF EXISTS / IF NOT EXISTS` checks.

Code-changes:
- `app/core/database.py`: nieuwe `assert_partner_api_keys_rls_ready` aanroep in lifespan na `assert_portal_users_rls_ready`.
- Update unit-tests die met `set_tenant`-bypassen werkten (verwijder fixtures die `app.current_org_id` op een fake org zetten zonder `WITH CHECK` te respecteren).

## Tests

- `test_rls_hygiene.py` (nieuw): per AC een fail-loud + happy-path test.
- Volledige regression-run op `tests/test_rls*.py` (bestaande RLS-tests).

## Operator-step

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-portal/backend/alembic/versions/post_deploy_<rev>_tenant_isolation_hygiene.sql
docker restart klai-core-portal-api-1
```

## Worktree

`klai-portal-rls-hygiene` — `feature/SPEC-TI-005-RLS-HYGIENE-PORTAL`.
