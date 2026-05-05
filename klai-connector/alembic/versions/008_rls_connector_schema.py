"""SPEC-SEC-CONNECTOR-RLS-001: RLS on connector.{connectors,sync_runs}

Closes audit finding TP-5 from reports/audit-2026-05-04/tenant-scoping.md:
both tables in the ``connector`` schema have ``org_id`` columns but no
PostgreSQL policy. Tenant isolation depended entirely on application-
level filters.

This migration intentionally has an empty body. All RLS DDL (ENABLE /
FORCE ROW LEVEL SECURITY + CREATE POLICY) requires the table-owner
role; under FORCE ROW LEVEL SECURITY even the owner is subject to the
policy at runtime. Keeping owner-required DDL out of migrations
prevents the ``alembic-cannot-drop-non-portal_api-tables`` crash-loop
documented in process-rules.md (extended on 2026-05-05 to cover
ENABLE / FORCE RLS + CREATE POLICY beyond the original DROP TABLE).

The DDL lives in the sibling SQL file
``alembic/versions/post_deploy_008.sql`` and must be applied as the
table owner (``klai``) via ``scripts/apply_post_deploy_sql.sh`` or
manually after every fresh deploy / DR restore. Pattern matches
portal-api's ``post_deploy_2f7d1eae1198.sql`` (#364).

Architectural decisions (per SPEC-SEC-CONNECTOR-RLS-001 v0.2.0):

- Category D (strict): empty GUC + no ``cross_org_admin`` flag returns
  zero rows / blocks INSERT with 42501. Loud-failure mode preferred
  over Category A's silent cross-tenant leakage.
- No role-split. ``FORCE ROW LEVEL SECURITY`` keeps the owner role
  subject to the policy. A separate runtime role would require a new
  SOPS env var with all the validator-env-parity risk that brings.
- ``app.cross_org_admin = '1'`` is the legitimate escape hatch for
  the lifespan reset, the SyncRunReaper periodic sweep, and the
  scheduler bootstrap that loads all tenant schedules. Bound
  exclusively via ``cross_org_session()`` in
  ``app/core/database.py`` — never from request-scoped code.

Revision ID: 008_rls_connector_schema
Revises: 007_sync_runs_fk
Create Date: 2026-05-05
"""

revision = "008_rls_connector_schema"
down_revision = "007_sync_runs_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op. DDL lives in post_deploy_008.sql.

    Apply manually as klai superuser:
        psql -f alembic/versions/post_deploy_008.sql
    Or via the shared helper:
        scripts/apply_post_deploy_sql.sh

    Without that step the tables have no policy and tenant isolation
    falls back to the application filter — the very condition this
    migration was written to fix. The smoke test in
    tests/test_rls_connector_schema.py verifies the policy is present
    on the live DB.
    """


def downgrade() -> None:
    """No-op. To roll back, run as klai superuser:

        DROP POLICY IF EXISTS tenant_isolation ON connector.connectors;
        DROP POLICY IF EXISTS tenant_isolation ON connector.sync_runs;
        ALTER TABLE connector.connectors DISABLE ROW LEVEL SECURITY;
        ALTER TABLE connector.sync_runs DISABLE ROW LEVEL SECURITY;

    The post-deploy SQL is idempotent and not part of the alembic-
    managed schema, so there is no automatic downgrade path.
    """
