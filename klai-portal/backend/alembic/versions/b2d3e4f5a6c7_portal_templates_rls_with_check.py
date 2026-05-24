"""portal_templates RLS: add explicit WITH CHECK clause (Finding C-1)

Revision ID: b2d3e4f5a6c7
Revises: a1c2d3e4f5b6
Create Date: 2026-05-24

Migration 34d8f876ffbf shipped the Cat-D helper pattern for USING but
omitted an explicit WITH CHECK clause. PostgreSQL reuses USING as an
implicit WITH CHECK on FOR ALL policies — which means WITH CHECK passes
ANY org_id when app.cross_org_admin=true. That turns the intentional
superuser read-bypass into a cross-tenant write hole.

This migration is intentionally a NO-OP in upgrade(): portal_templates
is owned by the 'klai' superuser, not 'portal_api' (the migration role).
CREATE/DROP POLICY on a table you do not own requires superuser — the
`alembic-cannot-drop-non-portal_api-tables` pitfall documented in
.claude/rules/klai/pitfalls/process-rules.md.

All DDL lives in post_deploy_b2d3e4f5a6c7_portal_templates_rls_with_check.sql.
Apply that file as the klai superuser AFTER the alembic migration completes:

    docker exec klai-core-postgres-1 psql -U klai -d klai -f /tmp/post_deploy.sql

The additive policy fix adds:
    WITH CHECK (org_id = _rls_current_org_id())

Full policy after fix:
    CREATE POLICY tenant_isolation ON portal_templates
        FOR ALL
        USING      (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
        WITH CHECK (org_id = _rls_current_org_id());

USING retains the IS NULL branch so cross-org admin reads (app.cross_org_admin=true)
continue to work. WITH CHECK is strict — a cross-org session can only write to the
tenant whose org_id matches the context GUC.

@MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-3
"""

from __future__ import annotations

from alembic import op  # noqa: F401 (alembic requires the import)

# revision identifiers, used by Alembic.
revision: str = "b2d3e4f5a6c7"
down_revision: str | None = "a1c2d3e4f5b6"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Intentionally empty: portal_templates is owned by the 'klai' superuser.
    # The CREATE POLICY DDL is in post_deploy_b2d3e4f5a6c7_portal_templates_rls_with_check.sql.
    # Run that file as klai superuser after this migration completes.
    # See: alembic-cannot-drop-non-portal_api-tables pitfall in process-rules.md
    pass


def downgrade() -> None:
    # Intentionally empty: downgrade would require dropping and recreating the
    # policy without WITH CHECK — that also requires klai superuser privileges.
    pass
