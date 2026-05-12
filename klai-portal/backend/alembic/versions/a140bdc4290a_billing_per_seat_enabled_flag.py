"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light) — per-tenant feature flag.

Adds ``portal_orgs.billing_per_seat_enabled`` as the per-tenant opt-in
gate for the future Moneybird per-seat-type billing migration.

The flag is FALSE for every existing tenant at upgrade time. Phase 5b
(a follow-up SPEC) wires the actual Moneybird mutation path behind the
flag — until then, a tenant admin clicking the FE "switch to per-user
billing" CTA hits a 501 stub that explains the staged rollout.

ADD COLUMN with NOT NULL DEFAULT is instant metadata in PG 11+ — same
RLS-safe shape as Phase 1's seat_type addition. portal_orgs is also
FORCE RLS Cat-A, so this matters: no row-write happens, no WITH CHECK
fires.

Revision ID: a140bdc4290a
Revises: 924465b9e0a6
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a140bdc4290a"
down_revision = "924465b9e0a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column(
            "billing_per_seat_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "billing_per_seat_enabled")
