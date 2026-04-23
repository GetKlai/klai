"""Add cross-schema FK from connector.sync_runs to public.portal_connectors.

SPEC-CONNECTOR-CLEANUP-001 REQ-04: restore referential integrity. The FK
from ``connector.sync_runs.connector_id`` to ``connector.connectors.id``
was dropped in ``004_remove_sync_run_fk`` and never re-bedraad. Since
SPEC-CONNECTOR-CLEANUP-001 the legacy ``connector.connectors`` table is
gone (``006_drop_connectors_table``); this migration adds a new FK to
``public.portal_connectors.id`` (the actual source of truth) with
``ON DELETE CASCADE`` so portal-side deletes automatically clean up
``connector.sync_runs`` rows.

The upgrade refuses to run if orphan ``sync_runs`` exist (rows whose
``connector_id`` does not match any ``portal_connectors.id``). This
prevents a half-applied state — operators must clean orphans first or
delete those sync_runs explicitly.

Cross-schema FKs are supported by Postgres but require ``REFERENCES``
privilege for the klai-connector role on ``public.portal_connectors``.
A missing privilege surfaces as ``permission denied for table
portal_connectors`` from Postgres; resolve with::

    GRANT REFERENCES ON public.portal_connectors TO <klai_connector_role>;

Revision ID: 007_sync_runs_fk_portal_connectors
Revises: 006_drop_connectors_table
Create Date: 2026-04-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_sync_runs_fk_portal_connectors"
down_revision: str | None = "006_drop_connectors_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FK_NAME = "fk_sync_runs_connector_id_portal_connectors"
_ORPHAN_QUERY = sa.text(
    """
    SELECT sr.id, sr.connector_id
    FROM connector.sync_runs sr
    LEFT JOIN public.portal_connectors pc ON pc.id = sr.connector_id
    WHERE pc.id IS NULL
    LIMIT 50
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    orphans = bind.execute(_ORPHAN_QUERY).fetchall()
    if orphans:
        sample_ids = ", ".join(str(row[0]) for row in orphans[:10])
        raise RuntimeError(
            f"Cannot add FK {_FK_NAME!r}: {len(orphans)} orphan sync_runs "
            "rows reference connector_ids with no matching public.portal_connectors. "
            f"First sync_run IDs: {sample_ids}. "
            "Delete them or repoint connector_id to a valid portal_connectors.id "
            "before re-running this migration."
        )

    op.create_foreign_key(
        constraint_name=_FK_NAME,
        source_table="sync_runs",
        referent_table="portal_connectors",
        local_cols=["connector_id"],
        remote_cols=["id"],
        source_schema="connector",
        referent_schema="public",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        _FK_NAME,
        "sync_runs",
        schema="connector",
        type_="foreignkey",
    )
