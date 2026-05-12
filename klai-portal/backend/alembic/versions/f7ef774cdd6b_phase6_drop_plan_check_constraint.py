"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 6 — drop portal_orgs_plan_check.

The plan-string CHECK constraint on ``portal_orgs.plan`` enforced the
``('free', 'chat', 'knowledge')`` value set since SPEC-PORTAL-PLAN-
RENAME-001. Phase 6 (2026-05-12) deprecates plan-driven decisions:
- Capability resolution moved to seat_type (Phase 4).
- Role assignment no longer gated by plan (Phase 3 removed
  ``assert_role_allowed_for_plan``).
- Billing tier is now ``portal_users.seat_type`` (Phase 1-2).

The ``plan`` column itself stays (it's still read by the legacy
billing path in ``app/api/billing.py`` and the Moneybird webhook
handler) — Phase 5b's follow-up SPEC drops the column after the
real per-seat-type Moneybird migration ships. Until then,
``plan`` is a free-form display field with no enforced shape.

Dropping the constraint is portal_api-safe — owner-of-table ALTER.

Revision ID: f7ef774cdd6b
Revises: a140bdc4290a
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "f7ef774cdd6b"
down_revision = "a140bdc4290a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DROP CONSTRAINT IF EXISTS so the upgrade is idempotent on a DB
    # that already had the constraint dropped manually.
    op.execute("ALTER TABLE portal_orgs DROP CONSTRAINT IF EXISTS portal_orgs_plan_check;")


def downgrade() -> None:
    # Restore the canonical plan ladder. ``free`` (sentinel) + ``chat``
    # (€28) + ``knowledge`` (€68) — matches the post-SPEC-PORTAL-PLAN-
    # RENAME-001 shape. If a tenant's plan value is outside this set
    # at downgrade time, the ADD CONSTRAINT will fail; reverting Phase 6
    # requires also reverting the rows.
    op.execute(
        "ALTER TABLE portal_orgs ADD CONSTRAINT portal_orgs_plan_check CHECK (plan IN ('free', 'chat', 'knowledge'));"
    )
