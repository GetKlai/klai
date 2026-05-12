"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1.

Adds the per-user billing axis (``portal_users.seat_type``) plus the
append-only seat-change audit table (``portal_user_seat_history``).
Trigger + RLS + per-row data-backfill live in the sibling post-deploy
SQL file ``post_deploy_f66c546c12eb.sql`` (klai-superuser path) —
portal_api cannot UPDATE FORCE-RLS-protected ``portal_users`` rows
without setting a tenant context per row (see
``rls-with-check-blocks-migration-update`` pitfall, added in this
hotfix).

Backfill mapping (matches ``app/core/seats.py::DEFAULT_SEAT_FOR_ROLE``):
    personal | company                       -> chat  (column DEFAULT)
    kb_manager | group_manager | admin       -> knowledge  (post-deploy UPDATE)

What changed vs the pre-hotfix shape:
  * The role -> seat backfill UPDATE was rejected on prod by the
    Cat-A inline-NULLIF RLS policy's WITH CHECK clause (which has NO
    "IS NULL permissive" branch — see ``rls-policy-shape-must-match-
    lifespan-assert``). With no ``app.current_org_id`` set inside the
    alembic transaction, every WITH CHECK predicate evaluates to NULL
    and the UPDATE fails with ``InsufficientPrivilegeError`` ->
    portal-api crashloops. Recovery on prod was a hand-applied
    klai-superuser SQL on 2026-05-12 that bypassed RLS; this file
    captures the correct shape so future deploys reproduce it.
  * ``ADD COLUMN ... NOT NULL DEFAULT 'chat'`` is instant metadata
    in PG 11+ — no row-rewrite happens, so no WITH CHECK fires. All
    existing rows inherit ``seat_type='chat'`` immediately. The
    role-based UPDATE that bumps KMs/admins to ``knowledge`` lives in
    post-deploy SQL.
  * History backfill + trigger creation also moved to post-deploy.
    The trigger gets installed AFTER the history backfill so the
    backfill INSERT does NOT trigger itself.
  * Intermediate state (alembic complete, post-deploy not yet applied):
    portal_users.seat_type exists with all rows at 'chat',
    portal_user_seat_history exists but empty. Python model resolves
    cleanly (SeatType.CHAT is valid). KMs/admins read as 'chat' until
    post-deploy runs — wrong-but-not-broken; admins see a slightly
    off /admin/billing/breakdown for ~5 min until the operator runs
    ``scripts/apply_post_deploy_sql.sh post_deploy_f66c546c12eb.sql``.

Revision ID: f66c546c12eb
Revises: c0d5e2a7b9f3
Create Date: 2026-05-12

Rebased on 2026-05-12 to chain off ``c0d5e2a7b9f3`` (see prior
revision comment in git history).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f66c546c12eb"
down_revision = "c0d5e2a7b9f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Add seat_type column to portal_users.
    #
    #    NOT NULL DEFAULT 'chat' is intentional — Postgres 11+ records the
    #    default as metadata and serves it lazily for existing rows, so the
    #    ALTER TABLE is instant (no row-rewrite) and no WITH CHECK fires.
    #    All existing rows immediately read as 'chat'. The post-deploy SQL
    #    upgrades KMs/admins to 'knowledge'.
    # -----------------------------------------------------------------------
    op.add_column(
        "portal_users",
        sa.Column(
            "seat_type",
            sa.String(length=16),
            nullable=False,
            server_default="chat",
        ),
    )
    op.create_check_constraint(
        "ck_portal_users_seat_type",
        "portal_users",
        "seat_type IN ('viewer', 'chat', 'knowledge')",
    )

    # -----------------------------------------------------------------------
    # 2. Create the empty seat-history table + indexes.
    #
    #    Trigger + RLS + initial backfill are post-deploy. The trigger
    #    must be installed AFTER the history backfill INSERT or it will
    #    fire on the backfill UPDATE (which we don't want).
    # -----------------------------------------------------------------------
    op.create_table(
        "portal_user_seat_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("portal_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("seat_type", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_by", sa.String(length=64), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "seat_type IN ('viewer', 'chat', 'knowledge')",
            name="ck_pu_seat_hist_seat_type",
        ),
    )
    op.create_index(
        "idx_pu_seat_hist_user_validto",
        "portal_user_seat_history",
        ["user_id", "valid_to"],
    )
    op.create_index(
        "idx_pu_seat_hist_org_validfrom",
        "portal_user_seat_history",
        ["org_id", "valid_from"],
    )
    # Partial-unique: at most one OPEN (current) row per user. The trigger
    # (created in post-deploy) depends on this — closes the open row
    # before inserting the new one. Concurrent UPDATEs race for this lock,
    # not for the table.
    op.create_index(
        "idx_pu_seat_hist_one_open_per_user",
        "portal_user_seat_history",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )

    # -----------------------------------------------------------------------
    # 3. Everything else (role-based UPDATE backfill, history backfill,
    #    trigger function + trigger, RLS + billing._rls_current_org_id
    #    helper) lives in alembic/versions/post_deploy_f66c546c12eb.sql.
    #    Applied by an operator as the klai superuser via
    #    ``scripts/apply_post_deploy_sql.sh post_deploy_f66c546c12eb.sql``.
    #
    #    Why this split:
    #      - portal_users has FORCE RLS with WITH CHECK clauses that
    #        require ``app.current_org_id`` to match each row's org_id.
    #        Migrations run without a tenant context -> WITH CHECK
    #        evaluates to NULL -> rows-violate-policy 500.
    #      - portal_user_seat_history needs ENABLE/FORCE RLS + CREATE
    #        POLICY, which require table-owner-or-superuser privileges
    #        the same way ENABLE RLS does (see
    #        ``alembic-cannot-drop-non-portal_api-tables`` pitfall).
    #    Both classes are addressed by running the rest as klai.
    # -----------------------------------------------------------------------


def downgrade() -> None:
    # Reverse-order teardown. Trigger + RLS objects are not alembic-
    # managed (post-deploy SQL); to fully revert RLS, the operator runs:
    #   DROP TRIGGER IF EXISTS portal_users_seat_history ON portal_users;
    #   DROP FUNCTION IF EXISTS portal_users_seat_history_trg() CASCADE;
    #   DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history;
    #   DROP FUNCTION IF EXISTS billing._rls_current_org_id();
    #   DROP SCHEMA IF EXISTS billing;
    # but the table is dropped here so the policy is moot.
    op.drop_index("idx_pu_seat_hist_one_open_per_user", table_name="portal_user_seat_history")
    op.drop_index("idx_pu_seat_hist_org_validfrom", table_name="portal_user_seat_history")
    op.drop_index("idx_pu_seat_hist_user_validto", table_name="portal_user_seat_history")
    op.drop_table("portal_user_seat_history")
    op.drop_constraint("ck_portal_users_seat_type", "portal_users", type_="check")
    op.drop_column("portal_users", "seat_type")
