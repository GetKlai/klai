"""widget allow_any_origin and loaded_origin columns — SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2

Per `alembic-cannot-drop-non-portal_api-tables` pitfall: `widgets` is owned
by the `klai` superuser (created via post-deploy SQL in earlier work), not
by `portal_api`. ALTER TABLE on the widgets table fails with
`InsufficientPrivilegeError: must be owner of table widgets` when run from
the entrypoint's `alembic upgrade head` (which runs as `portal_api`).

`widget_conversations` is in the same boat (Cat-D RLS, klai-owned per
post_deploy_a4f72e913c8b_widget_conversations_rls.sql).

So this alembic revision is intentionally a no-op marker — its only job is
to occupy the migration chain so a single head is preserved. The actual
ADD COLUMN statements live in the sibling post_deploy_a1c2d3e4f5b6.sql,
which the portal-api.yml deploy job applies as the klai superuser after
the alembic upgrade succeeds.

Revision ID: a1c2d3e4f5b6
Revises: 5b7c9d1e2f3a
Create Date: 2026-05-24
"""

revision = "a1c2d3e4f5b6"
down_revision = "5b7c9d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: schema work delegated to post_deploy_a1c2d3e4f5b6.sql (klai role).
    pass


def downgrade() -> None:
    # No-op: schema work delegated to post_deploy_a1c2d3e4f5b6.sql (klai role).
    pass
