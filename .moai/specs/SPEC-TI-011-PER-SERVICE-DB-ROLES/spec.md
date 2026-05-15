# SPEC-TI-011 — Per-service Postgres roles so RLS actually fires

**Audit ref:** Discovered 2026-05-06 during SPEC-TI-003 incident response
**Priority:** HIGH (the entire RLS-rollout audit is cosmetic on prod until this lands)
**Status:** Ready

## Goal

Move every klai service that today connects to Postgres as the `klai`
superuser to a dedicated **non-superuser, non-`bypassrls`** role. PostgreSQL
superusers always bypass RLS, regardless of `FORCE`. Today only `portal-api`
connects as a non-superuser (`portal_api`); the RLS policies added by
SPEC-TI-002 (connector), SPEC-TI-003 (knowledge), SPEC-TI-005 (portal
hygiene), SPEC-TI-010A (scribe) all exist in `pg_policies` but are never
evaluated for the services that own that data.

Confirmed on prod 2026-05-06:

```
postgres=> SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
            WHERE rolname IN ('klai','portal_api');
  rolname   | rolsuper | rolbypassrls
------------+----------+--------------
 klai       | t        | t
 portal_api | f        | f
```

## Acceptance criteria

- **AC-1** Four new roles exist on the postgres instance, each with
  `LOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, and a per-role password:
  - `connector_api`
  - `knowledge_ingest`
  - `retrieval_api`
  - `scribe_api`

- **AC-2** Each role has the minimum privileges needed for its service
  to function:
  - `USAGE` on its primary schema (e.g. `connector_api` on `connector`)
  - `CRUD` (`SELECT, INSERT, UPDATE, DELETE`) on every table in that
    schema
  - `EXECUTE` on every RLS helper function in `public` and the schema
  - `USAGE, SELECT, UPDATE` on every sequence in the schema
  - No `CREATE` on the schema (alembic migrations continue to run as
    `klai` — see operator step)

- **AC-3** SOPS contains four new env vars, one per service:
  - `CONNECTOR_POSTGRES_DSN` (used by klai-connector)
  - `KNOWLEDGE_INGEST_POSTGRES_DSN` (used by knowledge-ingest)
  - `RETRIEVAL_API_POSTGRES_DSN` (used by retrieval-api)
  - `SCRIBE_API_POSTGRES_DSN` (used by scribe-api)

  Each DSN uses its dedicated role + per-role password.

- **AC-4** `deploy/docker-compose.yml` passes the right DSN to each
  service via the `environment:` block. The legacy `POSTGRES_DSN`
  fallback (which uses `klai` superuser) is removed from these four
  services.

- **AC-5** Each service's pydantic config picks up the new DSN env var.
  Backward compatibility: if a service-specific DSN is unset, the
  service refuses to start with a fail-loud `ValidationError`. Do NOT
  silently fall back to the klai superuser DSN.

- **AC-6** Each service's lifespan logs the active DB role at startup
  and warns if `current_user` resolves to a superuser:

  ```python
  row = await conn.fetchrow("SELECT current_user, rolsuper, rolbypassrls "
                             "FROM pg_roles WHERE rolname = current_user")
  if row["rolsuper"] or row["rolbypassrls"]:
      logger.error("RLS bypass detected: connected as %s (super=%s, bypass=%s) — "
                   "tenant isolation is INACTIVE", row[0], row[1], row[2])
  ```

- **AC-7** Smoke test (CI): `tests/test_db_role_no_rls_bypass.py` per
  service connects with the dedicated DSN and asserts:
  - `current_user` is the expected role (not `klai`)
  - `rolsuper = false AND rolbypassrls = false`
  - A query without GUC against an RLS-protected table returns 0 rows
    (where today as klai it returns all rows)

- **AC-8** This SPEC depends on the wiring SPECs landing FIRST so the
  switch to the non-super role does not immediately produce 42501
  storms:
  - `SPEC-TI-003-FOLLOWUP-001` (knowledge-ingest pg_store wiring)
  - `SPEC-TI-002-FOLLOWUP-001` (connector sync_engine + scheduler)
  - `SPEC-TI-010A-FOLLOWUP-001` (scribe `set_tenant` infrastructure)

## Implementation skeleton

### Role-creation SQL (operator-applied as klai)

```sql
-- per service, with passwords from SOPS
CREATE ROLE connector_api WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :pw_connector;
CREATE ROLE knowledge_ingest WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :pw_knowledge;
CREATE ROLE retrieval_api WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :pw_retrieval;
CREATE ROLE scribe_api WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :pw_scribe;

-- per service, per schema
GRANT USAGE ON SCHEMA connector TO connector_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA connector TO connector_api;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA connector TO connector_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA connector
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO connector_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA connector
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO connector_api;
GRANT EXECUTE ON FUNCTION public._rls_current_org_text() TO connector_api;
-- (similar block per service+schema)
```

### Operator step

1. Generate four passwords (secret generator / `openssl rand`)
2. SOPS edit: add the four DSN env vars
3. Run the role-creation SQL on prod as klai
4. Push compose change + deploy
5. Verify: each service logs `current_user = <its-role>` at startup,
   no superuser warning, no 42501 spike in VictoriaLogs

## Rollback

```sql
-- per service
DROP ROLE IF EXISTS connector_api;
-- compose change reverts the DSN env var to fall back to klai-DSN
```

If a service fails fast on startup (AC-5), revert SOPS to remove the
service-specific DSN. The service then needs the legacy klai DSN to be
restored (or its config code reverted to fall back). Test in staging
first.

## Why this matters

Today every klai service that touches an RLS-protected table runs as
the `klai` superuser. A bug in the application layer that drops an
`org_id` filter would silently leak across tenants — RLS does NOT
catch it because superuser bypasses every policy. SPEC-TI-002 and
SPEC-TI-003 added policies but did not deliver the protection they
promised. This SPEC closes that gap.

Concrete proof on prod:

```
postgres=> SET ROLE klai;
SET
postgres=> SET app.current_org_id = '';
SET
postgres=> ALTER TABLE knowledge.artifacts FORCE ROW LEVEL SECURITY;
postgres=> SELECT COUNT(*) FROM knowledge.artifacts;  -- 1026 rows visible
```

After this SPEC, the same query as `knowledge_ingest` would either
raise 42501 (helper variant) or return 0 rows (NULLIF variant) — RLS
actually fires.
