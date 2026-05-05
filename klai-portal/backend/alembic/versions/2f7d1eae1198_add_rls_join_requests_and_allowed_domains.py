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

revision = "2f7d1eae1198"
down_revision = "rbac001drop00"
branch_labels = None
depends_on = None


# All DDL for this migration (ENABLE ROW LEVEL SECURITY + CREATE POLICY)
# requires the table-owner role (`klai` superuser), not the migration role
# (`portal_api`). PostgreSQL refuses these statements when run by anyone
# else with `ERROR: must be owner of table portal_join_requests` —
# crash-looping portal-api on the next deploy. Same class as
# `alembic-cannot-drop-non-portal_api-tables` in process-rules.md, just
# with ENABLE / FORCE ROW LEVEL SECURITY instead of DROP TABLE.
#
# The DDL has been moved to a sibling post-deploy SQL file:
#   alembic/versions/post_deploy_2f7d1eae1198.sql
#
# Apply it manually as the `klai` superuser after `alembic upgrade
# 2f7d1eae1198` completes (or via `scripts/apply_post_deploy_sql.sh`).
#
# Pattern matches: post_deploy_7e2d3c1a9b8f.sql (RLS for
# tenant_lifecycle_events), post_deploy_ed5b78b296f5.sql (DROP of
# portal_org_allowed_domains), post_deploy_f0a1b2c3d4e5.sql (RLS for
# widgets).
#
# This upgrade() is intentionally a no-op so the alembic_version row
# advances cleanly even when the operator has not yet applied the
# post-deploy SQL. Without that, every container start would re-attempt
# the failing DDL and crash-loop on every restart.


def upgrade() -> None:
    # No-op: see file-level comment. DDL lives in
    # post_deploy_2f7d1eae1198.sql, applied manually as klai superuser.
    pass


def downgrade() -> None:
    # No-op: the post-deploy SQL is idempotent and not part of the
    # alembic-managed schema. To roll back, run as klai superuser:
    #   DROP POLICY IF EXISTS tenant_isolation ON portal_join_requests;
    #   ALTER TABLE portal_join_requests DISABLE ROW LEVEL SECURITY;
    pass
