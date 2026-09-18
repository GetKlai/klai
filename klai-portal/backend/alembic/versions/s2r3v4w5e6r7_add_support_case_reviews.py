"""add portal_support_cases.reviews (human review of analysis)

SPEC-RAG-SUPPORT-GAP. Adds one nullable JSONB column that stores human reviews
of a case's machine analysis, keyed ``"{analysis_revision}:{finding_index}"``.

Production note: ``public.portal_support_cases`` is owned by the ``klai``
superuser with FORCE RLS enabled (set in
``post_deploy_s1p2c3a4s5e6_support_cases_rls.sql``). Alembic runs as
``portal_api``, which is not the owner and cannot ``ALTER`` the table, so the
real DDL lives in ``post_deploy_s2r3v4w5e6r7_support_case_reviews.sql`` and is
applied as ``klai`` after ``alembic upgrade head``. In dev/CI where the same
role owns the table, this migration adds the column directly. Pure additive,
metadata-only ``ADD COLUMN``: nullable, no default, no backfill — existing rows
read as ``NULL`` and the app treats that as ``{}``.

Revision ID: s2r3v4w5e6r7
Revises: s1p2c3a4s5e6
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s2r3v4w5e6r7"
down_revision = "s1p2c3a4s5e6"
branch_labels = None
depends_on = None


def _owns_table(conn: sa.engine.Connection) -> bool:
    owner = conn.execute(
        sa.text("SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='portal_support_cases'")
    ).scalar_one_or_none()
    current_user = conn.execute(sa.text("SELECT current_user")).scalar_one()
    return owner == current_user


def upgrade() -> None:
    conn = op.get_bind()
    if not _owns_table(conn):
        conn.execute(
            sa.text(
                "DO $$ BEGIN RAISE NOTICE "
                "'Skipping portal_support_cases.reviews migration. "
                "Apply post_deploy_s2r3v4w5e6r7_support_case_reviews.sql as klai.'; END $$;"
            )
        )
        return
    conn.execute(sa.text("ALTER TABLE portal_support_cases ADD COLUMN IF NOT EXISTS reviews jsonb"))


def downgrade() -> None:
    conn = op.get_bind()
    if not _owns_table(conn):
        conn.execute(
            sa.text(
                "DO $$ BEGIN RAISE NOTICE "
                "'Skipping portal_support_cases.reviews downgrade. Apply manually as table owner.'; END $$;"
            )
        )
        return
    conn.execute(sa.text("ALTER TABLE portal_support_cases DROP COLUMN IF EXISTS reviews"))
