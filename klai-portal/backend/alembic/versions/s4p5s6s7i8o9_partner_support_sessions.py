"""partner support sessions marker

Revision ID: s4p5s6s7i8o9
Revises: fa2b3c4d5e6f
Create Date: 2026-05-27

The runtime tables reference partner_api_keys and portal_orgs, and RLS/policy
DDL must be applied as the table owner. Keep Alembic as a marker and apply
post_deploy_s4p5s6s7i8o9_partner_support_sessions.sql after upgrade.
"""

from __future__ import annotations

revision: str = "s4p5s6s7i8o9"
down_revision: str | None = "fa2b3c4d5e6f"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
