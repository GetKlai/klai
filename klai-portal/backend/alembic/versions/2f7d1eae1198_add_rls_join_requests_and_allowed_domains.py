"""Add RLS policy on portal_join_requests

SPEC-SEC-PORTAL-RLS-001 — close the tenant-scoping gap surfaced by
``reports/audit-2026-05-04/tenant-scoping.md`` (TP-1).

``portal_join_requests`` already carries an ``org_id`` column but had no
``CREATE POLICY`` — meaning DB-layer tenant isolation was absent. This
migration brings it in line with the rest of the portal RLS regime
established in SPEC-SEC-003 / SPEC-SEC-007.

Policy shape: **Category A (auth-seed)**. The pre-auth ``auth_select.py``
INSERT-and-notify flow and the admin token-based ``auth_join`` approve
flow must look up / insert rows BEFORE any tenant context is known (the
request itself resolves WHICH org it belongs to). The policy mirrors the
``portal_users`` shape with the ``IS NULL`` permissive branch so the
pre-auth lookup succeeds when ``app.current_org_id`` is empty, while
admin-side queries (`admin/join_requests.py`) — which run after
`_get_caller_org` has bound a tenant — get strict ``org_id = T``
isolation.

**Why portal_org_allowed_domains is NOT in this migration**:
SPEC-AUTH-006 originally introduced ``portal_org_allowed_domains``, but
SPEC-AUTH-009 R2 (migration ``ed5b78b296f5``) replaced that table with
``portal_orgs.primary_domain`` + ``auto_accept_same_domain`` and DROPS
the table in production via ``post_deploy_ed5b78b296f5.sql``. Adding RLS
to a table that is already on the deletion path would pollute the
migration history without functional benefit. The original audit
finding TP-2 (``portal_org_allowed_domains`` missing RLS) is therefore
moot post-AUTH-009 — the table is gone in prod and only re-created on
``downgrade`` for reversibility. ``test_r2_removal.py`` is the
regression-guard for the absence.

The DDL uses the legacy inline-NULLIF pattern that matches the existing
migrations (``1b8736eb6455``, ``e669581d441f``). The post-deploy SQL
``post_deploy_rls_raise_on_missing_context.sql`` will later swap any
strict-category policies to the ``_rls_current_org_id()`` helper for
fail-loud behaviour — but ``portal_join_requests`` is Category A, not
Category D, so it stays on the inline pattern.

Idempotency: ``CREATE POLICY`` does not support ``IF NOT EXISTS``, so
the upgrade is wrapped in ``DROP POLICY IF EXISTS`` + ``CREATE POLICY``.
Running the migration on a database that already has ``tenant_isolation``
defined (e.g. partially migrated staging) is therefore safe.

Revision ID: 2f7d1eae1198
Revises: rbac001drop00
Create Date: 2026-05-05
"""

from alembic import op

revision = "2f7d1eae1198"
down_revision = "rbac001drop00"
branch_labels = None
depends_on = None

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_T_IS_NULL = "NULLIF(current_setting('app.current_org_id', true), '') IS NULL"


def _enable_rls(table: str) -> None:
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
    )


def upgrade() -> None:
    # portal_join_requests: Category A (permissive on missing context).
    # The admin token-based approve flow in app/api/auth_join.py looks up
    # the join request by its approval_token BEFORE any tenant context is
    # resolved (the token IS the resolution mechanism). Same shape as
    # portal_users / portal_connectors — a permissive IS NULL branch so
    # the pre-auth lookup succeeds, with strict tenant-equality once
    # set_tenant has fired downstream (admin/join_requests.py path).
    _enable_rls("portal_join_requests")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "DROP POLICY IF EXISTS tenant_isolation ON portal_join_requests"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "CREATE POLICY tenant_isolation ON portal_join_requests "
        f"USING (org_id = {_T} OR {_T_IS_NULL}) "
        f"WITH CHECK (org_id = {_T})"
    )


def downgrade() -> None:
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "DROP POLICY IF EXISTS tenant_isolation ON portal_join_requests"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        "ALTER TABLE portal_join_requests DISABLE ROW LEVEL SECURITY"
    )
