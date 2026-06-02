"""add litellm_team_id and zitadel_oidc_app_id to portal_orgs

Revision ID: e9f1a2b3c4d5
Revises: sh2f2e3d4c5b6
Create Date: 2026-06-02 00:00:00.000000

SPEC-INFRA-TENANT-DELETE H2 — persist the external resource IDs at
provisioning time so deprovisioning deletes the exact LiteLLM team and
Zitadel OIDC app instead of resolving them via fuzzy list lookups. The
lookup path returned "" (treated as "confirmed absent → skip") on any
false-negative response (alias-filter ignored, pagination, legacy app
naming), silently orphaning the team/app.

Both columns are nullable: legacy rows provisioned before this migration
stay NULL and the deprovisioning orchestrator falls back to the resolve
path for them. ``portal_orgs`` is NOT an RLS-FORCE table (absent from
``app.core.rls_guard.RLS_DML_TABLES``), so a nullable ``ADD COLUMN`` is an
instant metadata operation — no per-row write, no WITH CHECK, safe in
``upgrade()`` with no post-deploy SQL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "sh2f2e3d4c5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("portal_orgs", sa.Column("litellm_team_id", sa.String(128), nullable=True))
    op.add_column("portal_orgs", sa.Column("zitadel_oidc_app_id", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("portal_orgs", "zitadel_oidc_app_id")
    op.drop_column("portal_orgs", "litellm_team_id")
