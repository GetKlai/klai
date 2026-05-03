"""add_deprovision_states_and_lifecycle_table

SPEC-INFRA-TENANT-DELETE-001 R2 + R6.

Three changes wrapped in one migration because they ship together:

1. Extend `ck_portal_orgs_provisioning_status` CHECK constraint with three new
   values: 'deprovisioning', 'deprovisioned', 'failed_deprovisioning'. These
   model the tenant-delete state machine (R2). Drop + recreate via the standard
   pattern; no data backfill needed because no existing row carries these values.

2. Add `last_failure JSONB NULL` column to `portal_orgs`. Populated by the
   deprovisioning orchestrator on definitive step failure with
   `{"step": <name>, "error": <truncated>, "attempt": 3, "failed_at": <iso>}`.
   Cleared on successful retry. NULL on every other state.

3. Create `tenant_lifecycle_events` table (R6). NO foreign key to portal_orgs
   by design — survives the hard-delete that ends a deprovisioning run.
   Snapshot fields preserve the org identity at the moment of the lifecycle
   event so audit queries 6 months later can still answer "what was tenant X
   called and when did it leave?".

RLS policy on tenant_lifecycle_events is added in a separate
post_deploy_*.sql file because portal_api lacks the role to ALTER TABLE
ENABLE ROW LEVEL SECURITY (see portal-security.md "RLS + Alembic").

Revision ID: 7e2d3c1a9b8f
Revises: 13bb3bb00d53
Create Date: 2026-05-03 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "7e2d3c1a9b8f"
down_revision: Union[str, Sequence[str], None] = "13bb3bb00d53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Existing values from migration 32fc0ed3581b plus three deprovision states.
# Keep this list in sync with `ALLOWED_STATUSES` in state_machine.py — a
# divergence between the CHECK constraint and the orchestrator's transitions
# manifests as a runtime IntegrityError on the first deprovisioning run.
_ALLOWED_STATUSES = (
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
    # SPEC-INFRA-TENANT-DELETE-001 R2 — three new deprovisioning states
    "deprovisioning",
    "deprovisioned",
    "failed_deprovisioning",
)


def upgrade() -> None:
    # 1. Replace CHECK constraint with extended allowed-values list.
    op.drop_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        type_="check",
    )
    allowed_list = ", ".join(f"'{s}'" for s in _ALLOWED_STATUSES)
    op.create_check_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        f"provisioning_status IN ({allowed_list})",
    )

    # 2. Add last_failure column for deprovisioning failure detail.
    op.add_column(
        "portal_orgs",
        sa.Column("last_failure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 3. Create tenant_lifecycle_events table.
    op.create_table(
        "tenant_lifecycle_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("org_id_snapshot", sa.Integer(), nullable=False),
        sa.Column("org_slug_snapshot", sa.Text(), nullable=False),
        sa.Column("org_name_snapshot", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('provisioned', 'deprovisioned', 'failed_deprovisioning')",
            name="ck_tenant_lifecycle_events_event_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('owner', 'platform_admin', 'system')",
            name="ck_tenant_lifecycle_events_actor_type",
        ),
    )
    op.create_index(
        "ix_tenant_lifecycle_events_org_slug",
        "tenant_lifecycle_events",
        ["org_slug_snapshot"],
    )
    op.create_index(
        "ix_tenant_lifecycle_events_created_at",
        "tenant_lifecycle_events",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    # Reverse order of upgrade.

    # 3. Drop lifecycle table + indexes.
    op.drop_index("ix_tenant_lifecycle_events_created_at", table_name="tenant_lifecycle_events")
    op.drop_index("ix_tenant_lifecycle_events_org_slug", table_name="tenant_lifecycle_events")
    op.drop_table("tenant_lifecycle_events")

    # 2. Drop last_failure column.
    op.drop_column("portal_orgs", "last_failure")

    # 1. Revert CHECK constraint to pre-deprovision allowed-list. Any row that
    #    happens to be in a deprovisioning state when the downgrade runs would
    #    violate the old constraint — fold them into a recoverable state first.
    op.execute(
        "UPDATE portal_orgs "
        "SET provisioning_status = 'failed_rollback_pending', "
        "    last_failure = NULL "
        "WHERE provisioning_status IN ('deprovisioning', 'deprovisioned', 'failed_deprovisioning')"
    )
    op.drop_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        type_="check",
    )
    legacy_allowed = (
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
    legacy_list = ", ".join(f"'{s}'" for s in legacy_allowed)
    op.create_check_constraint(
        "ck_portal_orgs_provisioning_status",
        "portal_orgs",
        f"provisioning_status IN ({legacy_list})",
    )
