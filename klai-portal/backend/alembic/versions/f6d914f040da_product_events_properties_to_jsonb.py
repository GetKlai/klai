"""schema: product_events.properties json -> jsonb

Revision ID: f6d914f040da
Revises: e44f9da674fe
Create Date: 2026-05-07

The SQLAlchemy model ``ProductEvent.properties`` declared ``JSONB`` but
the live column was created as ``json``. The drift went unnoticed until
the new KB stats endpoints (PR #508 + #510 + #513) were the first read
path to use jsonb-only operators (``@>``, ``jsonb_array_elements_text``,
``jsonb_typeof``). Until this migration lands, every stats query has to
spell ``(properties::jsonb)`` to coerce per-row.

``portal_api`` owns ``product_events`` (verified via ``pg_tables``), so
this ALTER COLUMN runs through standard alembic. Existing event payloads
all serialize cleanly to jsonb — no data transformation needed beyond
the ``USING`` cast.

After this migration lands the ad-hoc casts in
``app/api/app_knowledge_bases.py`` are removed in the same PR.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "f6d914f040da"
down_revision = "e44f9da674fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSONB is a strict superset of JSON for our payloads — every existing
    # row deserialises and re-serialises identically. The USING clause is
    # required because PostgreSQL won't auto-coerce json -> jsonb.
    op.execute("ALTER TABLE product_events ALTER COLUMN properties TYPE jsonb USING properties::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE product_events ALTER COLUMN properties TYPE json USING properties::json")
