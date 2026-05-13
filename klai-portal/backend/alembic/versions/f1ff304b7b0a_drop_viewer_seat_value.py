"""SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 — drop 'viewer' from seat-type CHECKs.

Removes the ``'viewer'`` value from the two CHECK constraints that
constrain seat-type values:

  - ``portal_users.ck_portal_users_seat_type``
  - ``portal_user_seat_history.ck_pu_seat_hist_seat_type``

Pre-flight verified on prod 2026-05-13: ``SELECT seat_type, COUNT(*)
FROM portal_users GROUP BY seat_type`` returns ``knowledge: 10, chat:
2`` (zero viewer rows). Same for portal_user_seat_history. Safe to
narrow the CHECK without a data backfill.

Both ALTER TABLE DROP CONSTRAINT + ADD CONSTRAINT are portal_api-safe
(portal_api owns both tables — verified earlier in the audit-2026-05-12
ownership pass).

Why this lands as a separate migration (not folded into Phase 1's
f66c546c12eb): Phase 1 is already deployed on prod. Modifying the
already-applied migration in-place is the
``alembic-stamped-past-skipped-migration`` anti-pattern. New revision
is the canonical fix.

Revision ID: f1ff304b7b0a
Revises: f7ef774cdd6b
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op

revision = "f1ff304b7b0a"
down_revision = "f7ef774cdd6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # portal_users.seat_type
    op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS ck_portal_users_seat_type;")
    op.create_check_constraint(
        "ck_portal_users_seat_type",
        "portal_users",
        "seat_type IN ('chat', 'knowledge')",
    )

    # portal_user_seat_history.seat_type
    op.execute("ALTER TABLE portal_user_seat_history DROP CONSTRAINT IF EXISTS ck_pu_seat_hist_seat_type;")
    op.create_check_constraint(
        "ck_pu_seat_hist_seat_type",
        "portal_user_seat_history",
        "seat_type IN ('chat', 'knowledge')",
    )


def downgrade() -> None:
    # Restore the three-value set (chat + knowledge + viewer). If any
    # row's seat_type is outside this set at downgrade time, the ADD
    # CONSTRAINT will fail; the operator must reconcile the data first.
    op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS ck_portal_users_seat_type;")
    op.create_check_constraint(
        "ck_portal_users_seat_type",
        "portal_users",
        "seat_type IN ('viewer', 'chat', 'knowledge')",
    )
    op.execute("ALTER TABLE portal_user_seat_history DROP CONSTRAINT IF EXISTS ck_pu_seat_hist_seat_type;")
    op.create_check_constraint(
        "ck_pu_seat_hist_seat_type",
        "portal_user_seat_history",
        "seat_type IN ('viewer', 'chat', 'knowledge')",
    )
