"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (light) — per-tenant feature flag.

Adds ``portal_orgs.billing_per_seat_enabled`` as the per-tenant opt-in
gate for the future Moneybird per-seat-type billing migration.

The flag is FALSE for every existing tenant at upgrade time. Phase 5b
(a follow-up SPEC) wires the actual Moneybird mutation path behind the
flag — until then, a tenant admin clicking the FE "switch to per-user
billing" CTA hits a 501 stub that explains the staged rollout.

ADD COLUMN with NOT NULL DEFAULT is instant metadata in PG 11+ —
no row-rewrite happens, the alembic transaction commits in
milliseconds.

RLS note: ``portal_orgs`` has **no** RLS enabled at all (verified on
prod 2026-05-13: ``relrowsecurity=false``, ``relforcerowsecurity=
false``, ``owner=portal_api``). The earlier draft of this docstring
claimed it was Cat-A like ``portal_users`` — that was wrong. The
migration would have been safe either way (DDL doesn't fire WITH
CHECK), but the docstring is now accurate. Phase 5b's eventual
UPDATE on ``portal_orgs.billing_per_seat_enabled`` won't hit the
WITH-CHECK trap that bit Phase 1's portal_users backfill — there
is no policy to fire.

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
