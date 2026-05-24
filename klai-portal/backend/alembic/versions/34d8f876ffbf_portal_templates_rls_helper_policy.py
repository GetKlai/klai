"""portal_templates RLS: inline NULLIF -> _rls_current_org_id() helper

Revision ID: 34d8f876ffbf
Revises: a4f72e913c8b
Create Date: 2026-05-22

Upgrades the ``portal_templates`` tenant-isolation policy from the inline
NULLIF pattern to the canonical Category-D helper pattern used by every
other strict tenant table (``portal_knowledge_bases`` et al.):

    OLD: org_id = NULLIF(current_setting('app.current_org_id', true), '')::int
    NEW: _rls_current_org_id() IS NULL OR org_id = _rls_current_org_id()

Why: the inline pattern silently returns zero rows when no tenant context
is set and does NOT honour ``app.cross_org_admin`` — so platform-admin
cross-tenant reads via ``cross_org_session()`` saw nothing. The helper
pattern (a) raises 42501 on missing context instead of silent-empty
(the documented hardening direction), and (b) returns NULL when
``app.cross_org_admin='true'`` so a cross-org session matches all rows.
portal_templates was the only Cat-D table still on the inline pattern.

Behaviour-preserving for normal consumers: when ``app.current_org_id``
is set, both old and new policies evaluate to ``org_id = <ctx>``. All
six readers (app_templates, app_account, internal, partner,
default_templates, deprovisioning_steps) set tenant context before
touching the table — verified 2026-05-22.

Safe to run inside the alembic migration (unlike most RLS DDL): the
table is owned by ``portal_api`` (the migration role) and ``portal_api``
has EXECUTE on ``_rls_current_org_id()`` — both verified against prod.
No post-deploy SQL / klai superuser step required.

@MX:SPEC: SPEC-PLATFORM-ADMIN-001
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "34d8f876ffbf"
down_revision: str | None = "a4f72e913c8b"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ENABLE/FORCE are idempotent; the policy needs DROP+CREATE because
    # PostgreSQL has no CREATE OR REPLACE POLICY.
    op.execute("ALTER TABLE portal_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_templates FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_templates")
    op.execute(
        "CREATE POLICY tenant_isolation ON portal_templates "
        "USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_templates")
    op.execute(
        "CREATE POLICY tenant_isolation ON portal_templates "
        "USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::int)"
    )
