"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1.

Adds the per-user billing axis (``portal_users.seat_type``) plus the
append-only seat-change audit table (``portal_user_seat_history``) and
the trigger that maintains it. RLS for the new table lives in the
sibling post-deploy SQL file ``post_deploy_f66c546c12eb.sql`` (klai-
superuser path; portal_api cannot ENABLE RLS on its own — see
``alembic-cannot-drop-non-portal_api-tables`` pitfall, which extends to
``ENABLE / FORCE ROW LEVEL SECURITY`` and ``CREATE POLICY``).

Backfill mapping (matches ``app/core/seats.py::DEFAULT_SEAT_FOR_ROLE``):
    personal | company                       -> chat
    kb_manager | group_manager | admin       -> knowledge
    (any other / unknown role string)        -> chat   (cheapest non-zero)

Why a Postgres trigger and not a SQLAlchemy event listener: the listener
misses ``session.execute(update(PortalUser).values(...))`` bulk paths
(several admin scripts use those) AND races on concurrent UPDATEs of the
same user-row. The trigger fires on every UPDATE regardless of how it
arrived and runs in the same transaction. The partial-unique index
``idx_pu_seat_hist_one_open_per_user`` serializes concurrent writes.

Revision ID: f66c546c12eb
Revises: e0ad7c2b1e80
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f66c546c12eb"
down_revision = "e0ad7c2b1e80"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Trigger function body. Defined as a module-level constant so the
# upgrade() and downgrade() helpers can reference it without escaping.
# ---------------------------------------------------------------------------

# Fires AFTER INSERT OR UPDATE on portal_users. On INSERT, snapshots the
# initial state with change_reason='invite'. On UPDATE, closes the
# previous open row (idx_pu_seat_hist_one_open_per_user enforces "exactly
# one open row per user", so the WHERE clause matches at most one row)
# and inserts a new open row with the appropriate change_reason. Only
# audited columns trigger the append (IS DISTINCT FROM handles NULL).
_TRIGGER_FUNCTION_BODY = """
CREATE OR REPLACE FUNCTION portal_users_seat_history_trg() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(), 'invite');
        RETURN NEW;
    END IF;
    -- UPDATE path: only fire when an audited column changed
    IF (NEW.seat_type IS DISTINCT FROM OLD.seat_type)
       OR (NEW.role     IS DISTINCT FROM OLD.role)
       OR (NEW.status   IS DISTINCT FROM OLD.status) THEN
        -- Close the previous (current) row. The partial-unique index
        -- guarantees at most one row matches.
        UPDATE portal_user_seat_history
           SET valid_to = NOW()
         WHERE user_id = NEW.id
           AND valid_to IS NULL;
        -- Append the new current row, attributing the change to the
        -- column that most-recently moved (precedence: seat > role > status
        -- so a combined PATCH still records a meaningful reason).
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(),
             CASE
                 WHEN NEW.seat_type IS DISTINCT FROM OLD.seat_type THEN 'seat_change'
                 WHEN NEW.role      IS DISTINCT FROM OLD.role      THEN 'role_change'
                 ELSE 'status_change'
             END);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Add seat_type column to portal_users (nullable for backfill window).
    # -----------------------------------------------------------------------
    op.add_column(
        "portal_users",
        sa.Column("seat_type", sa.String(length=16), nullable=True),
    )

    # -----------------------------------------------------------------------
    # 2. Backfill from role. Mirrors DEFAULT_SEAT_FOR_ROLE in seats.py.
    # -----------------------------------------------------------------------
    op.execute(
        """
        UPDATE portal_users
           SET seat_type = CASE
               WHEN role IN ('kb_manager', 'group_manager', 'admin') THEN 'knowledge'
               WHEN role IN ('personal', 'company')                  THEN 'chat'
               ELSE 'chat'
           END
        ;
        """
    )

    # -----------------------------------------------------------------------
    # 3. Lock the column down: NOT NULL + default + CHECK constraint.
    # -----------------------------------------------------------------------
    op.alter_column(
        "portal_users",
        "seat_type",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="chat",
    )
    op.create_check_constraint(
        "ck_portal_users_seat_type",
        "portal_users",
        "seat_type IN ('viewer', 'chat', 'knowledge')",
    )

    # -----------------------------------------------------------------------
    # 4. Create the seat-history table.
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
    # below depends on this — closes the open row before inserting the new
    # one. Concurrent UPDATEs race for this lock, not for the table.
    op.create_index(
        "idx_pu_seat_hist_one_open_per_user",
        "portal_user_seat_history",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )

    # -----------------------------------------------------------------------
    # 5. Backfill the history: one row per existing portal_users entry,
    #    valid_from = the user's created_at, valid_to NULL (current).
    # -----------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        SELECT id, org_id, seat_type, role::text, status::text, created_at, 'backfill'
          FROM portal_users
        ;
        """
    )

    # -----------------------------------------------------------------------
    # 6. Install the trigger function + trigger. From here on, every
    #    INSERT/UPDATE on portal_users is mirrored into portal_user_seat_history.
    # -----------------------------------------------------------------------
    op.execute(_TRIGGER_FUNCTION_BODY)
    op.execute(
        """
        DROP TRIGGER IF EXISTS portal_users_seat_history ON portal_users;
        CREATE TRIGGER portal_users_seat_history
            AFTER INSERT OR UPDATE ON portal_users
            FOR EACH ROW EXECUTE FUNCTION portal_users_seat_history_trg();
        """
    )

    # -----------------------------------------------------------------------
    # 7. RLS for portal_user_seat_history lives in
    #    alembic/versions/post_deploy_f66c546c12eb.sql — applied as klai
    #    superuser via scripts/apply_post_deploy_sql.sh. portal_api
    #    cannot ENABLE / FORCE RLS on a table even if it owns it (same
    #    class as alembic-cannot-drop-non-portal_api-tables).
    # -----------------------------------------------------------------------


def downgrade() -> None:
    # Reverse-order teardown. Trigger first (depends on the table), then
    # the table, then the column.
    op.execute("DROP TRIGGER IF EXISTS portal_users_seat_history ON portal_users")
    op.execute("DROP FUNCTION IF EXISTS portal_users_seat_history_trg() CASCADE")
    op.drop_index("idx_pu_seat_hist_one_open_per_user", table_name="portal_user_seat_history")
    op.drop_index("idx_pu_seat_hist_org_validfrom", table_name="portal_user_seat_history")
    op.drop_index("idx_pu_seat_hist_user_validto", table_name="portal_user_seat_history")
    op.drop_table("portal_user_seat_history")
    op.drop_constraint("ck_portal_users_seat_type", "portal_users", type_="check")
    op.drop_column("portal_users", "seat_type")
    # RLS objects in post_deploy_f66c546c12eb.sql are not alembic-managed;
    # to fully revert RLS, the operator runs:
    #   DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history;
    #   DROP FUNCTION IF EXISTS billing._rls_current_org_id();
    #   DROP SCHEMA IF EXISTS billing;
    # but the table is gone here so the policy is moot.
