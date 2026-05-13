"""change portal_users unique constraint for multi-org support

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-04-16
"""

from alembic import op

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old global unique on zitadel_user_id.
    #
    # The parent migration `d64fdcfecf32_create_portal_orgs_and_portal_users`
    # creates uniqueness via `op.create_index(..., unique=True)` (a UNIQUE
    # INDEX, tracked in pg_indexes), but production also has a UNIQUE
    # CONSTRAINT named `portal_users_zitadel_user_id_key` (tracked in
    # pg_constraint) that was either added via a parallel SPEC branch or
    # manually before this migration ran in 2026-04-16. On fresh installs
    # only the index exists — the constraint never did — so the original
    # `op.drop_constraint(...)` raised `UndefinedObjectError` and rolled
    # back the entire `alembic upgrade head` transaction.
    #
    # Fix: use raw SQL with `DROP CONSTRAINT IF EXISTS`, idempotent on
    # both prod (drops the constraint) and fresh installs (no-op). The
    # unique INDEX drop below still removes the underlying uniqueness.
    # See `docs/runbooks/local-dev.md` troubleshooting + pitfall
    # `alembic-multi-pr-head-split` in `.claude/rules/klai/pitfalls/process-rules.md`.
    op.drop_index("ix_portal_users_zitadel_user_id", table_name="portal_users")
    op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS portal_users_zitadel_user_id_key")

    # Add composite unique constraint: one user per org
    op.create_unique_constraint(
        "uq_portal_users_zitadel_user_org",
        "portal_users",
        ["zitadel_user_id", "org_id"],
    )
    # Re-create index (non-unique now)
    op.create_index("ix_portal_users_zitadel_user_id", "portal_users", ["zitadel_user_id"])


def downgrade() -> None:
    op.drop_index("ix_portal_users_zitadel_user_id", table_name="portal_users")
    op.drop_constraint("uq_portal_users_zitadel_user_org", table_name="portal_users", type_="unique")
    op.create_index("ix_portal_users_zitadel_user_id", "portal_users", ["zitadel_user_id"], unique=True)
