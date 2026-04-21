"""Add provisioning state machine: deleted_at soft-delete, partial unique slug index, CHECK constraint.

SPEC-PROV-001 M1.

This migration does four things in one revision, in this order:

1. Adds `portal_orgs.deleted_at TIMESTAMPTZ NULL` (soft-delete column).
2. Drops the existing full `ix_portal_orgs_slug` unique index and replaces it with a
   partial unique index `ix_portal_orgs_slug_active` that enforces uniqueness only over
   non-soft-deleted rows (`WHERE deleted_at IS NULL`). This is the industry standard
   SaaS pattern (Linear/Notion/GitLab) for releasing a unique identifier on failure.
3. Performs inline UPDATE statements for the two known test-orgs (Voys and Klai) to
   map any legacy `'active'` values to `'ready'`, and a fail-safe UPDATE to re-map any
   unexpected `'failed'` rows to `'failed_rollback_pending'` so they are visible in
   Grafana under the new state machine. The operator must run `SELECT id, slug,
   provisioning_status FROM portal_orgs` on production before deploy to verify these
   two UPDATE statements cover the actual data.
4. Adds a CHECK constraint `ck_portal_orgs_provisioning_status` enforcing the new state
   machine values. Legacy `'pending'` is retained (used between signup and
   BackgroundTask-start). Legacy `'failed'` is explicitly NOT in the allowed set —
   the fail-safe UPDATE in step 3 migrates any existing rows; after deploy the
   orchestrator writes `failed_rollback_pending` / `failed_rollback_complete` instead.

Revision ID: p1r2o3v4s5b1
Revises: f0a1b2c3d4e5
Create Date: 2026-04-21
"""

from alembic import op
import sqlalchemy as sa

revision = "p1r2o3v4s5b1"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


ALLOWED_STATUSES = (
    "pending",
    "queued",
    "creating_zitadel_app",
    "creating_litellm_team",
    "creating_mongo_user",
    "writing_env_file",
    "creating_personal_kb",
    "creating_portal_kbs",
    "starting_container",
    "writing_caddyfile",
    "reloading_caddy",
    "creating_system_groups",
    "ready",
    "failed_rollback_pending",
    "failed_rollback_complete",
)


def upgrade() -> None:
    # 1. Add deleted_at column for soft-delete.
    op.add_column(
        "portal_orgs",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 1b. Add updated_at column for the M7 stuck-detector (needs a per-row
    #     freshness marker to distinguish a run that is still alive from one
    #     that was abandoned by a crashed portal-api).
    op.add_column(
        "portal_orgs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # 2. Replace full unique index on slug with partial unique index over active rows.
    op.drop_index("ix_portal_orgs_slug", table_name="portal_orgs")
    op.create_index(
        "ix_portal_orgs_slug_active",
        "portal_orgs",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 3. Inline data migration for the two known test-orgs + fail-safe for 'failed' rows.
    #    Operator must verify with `SELECT id, slug, provisioning_status FROM portal_orgs`
    #    before deploying that these UPDATEs cover the actual state of the database.
    op.execute(
        "UPDATE portal_orgs "
        "SET provisioning_status = 'ready' "
        "WHERE slug IN ('voys', 'klai') "
        "AND provisioning_status IN ('active', 'ready')"
    )
    op.execute(
        "UPDATE portal_orgs "
        "SET provisioning_status = 'failed_rollback_pending' "
        "WHERE provisioning_status = 'failed'"
    )

    # 4. CHECK constraint enforcing the new state machine values.
    allowed_list = ", ".join(f"'{s}'" for s in ALLOWED_STATUSES)
    op.create_check_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        f"provisioning_status IN ({allowed_list})",
    )


def downgrade() -> None:
    # Reverse order of upgrade.
    op.drop_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        type_="check",
    )

    # Data rollback: map new-state values back to legacy 'failed' for forward-compat.
    # 'ready' stays 'ready'. Intermediate states and rollback states → 'failed'.
    op.execute(
        "UPDATE portal_orgs "
        "SET provisioning_status = 'failed' "
        "WHERE provisioning_status IN ("
        "'creating_zitadel_app', 'creating_litellm_team', 'creating_mongo_user', "
        "'writing_env_file', 'creating_personal_kb', 'creating_portal_kbs', "
        "'starting_container', 'writing_caddyfile', 'reloading_caddy', "
        "'creating_system_groups', 'queued', "
        "'failed_rollback_pending', 'failed_rollback_complete'"
        ")"
    )

    # Drop partial unique index and restore full unique index.
    op.drop_index("ix_portal_orgs_slug_active", table_name="portal_orgs")
    op.create_index(
        "ix_portal_orgs_slug",
        "portal_orgs",
        ["slug"],
        unique=True,
    )

    # Drop updated_at and deleted_at columns (reverse of upgrade).
    op.drop_column("portal_orgs", "updated_at")
    op.drop_column("portal_orgs", "deleted_at")
