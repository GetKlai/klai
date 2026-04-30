"""Add cross-schema FK ``connector.sync_runs.connector_id`` -> ``public.portal_connectors.id`` ON DELETE CASCADE.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-08 / SPEC-CONNECTOR-CLEANUP-001 REQ-04.

Background. ``connector.sync_runs`` originally had an FK to
``connector.connectors.id``. SPEC-CONNECTOR-CLEANUP-001 dropped
``connector.connectors`` (legacy table), and migration ``004_remove_sync_run_fk``
removed the FK altogether. Since then orphan ``sync_runs`` rows could only be
prevented by application-level discipline; the portal-side
``klai_connector_client.delete_sync_runs`` introduced in PR #244 was the
interim fence.

This migration restores referential integrity by pointing the FK at the new
canonical owner — ``public.portal_connectors`` — with ``ON DELETE CASCADE``,
so deleting a connector row in the portal automatically removes its sync
history. The interim app-level call becomes a redundant safety net and can
be removed in a follow-up commit.

Pre-flight. The migration runs as the role configured for klai-connector's
``DATABASE_URL`` (typically ``klai`` superuser in dev; ``connector_api`` or
similar in prod). That role MUST have ``REFERENCES`` privilege on
``public.portal_connectors``. In prod we grant this once, before running
the migration:

    -- as klai superuser:
    GRANT REFERENCES ON public.portal_connectors TO connector_api;

If the grant is missing the migration fails with a clear permission-denied
error rather than producing a silently-broken FK.

Orphan cleanup. Pre-existing rows in ``connector.sync_runs`` whose
``connector_id`` no longer exists in ``portal_connectors`` would block the
``ALTER TABLE ... ADD CONSTRAINT`` step. We delete them in the same
transaction immediately before adding the constraint. These are operational
audit records pointing at long-gone connectors — losing them is acceptable
(SPEC REQ-08.3); ``sync_runs`` is not source-of-truth user data.

Post-migration: ``connector.sync_runs.org_id`` (added in migration 006) is
preserved. Pre-migration-006 rows with ``org_id IS NULL`` still cannot be
queried per-tenant (matches the deliberate trade-off documented in 006);
the FK CASCADE applies regardless of whether ``org_id`` is set.

Revision ID: 007_sync_runs_fk_portal_connectors
Revises: 006_add_org_id_to_sync_runs
Create Date: 2026-04-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007_sync_runs_fk_portal_connectors"
down_revision: str | None = "006_add_org_id_to_sync_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: drop orphan sync_runs whose connector_id is not in portal_connectors.
    # Without this the FK ADD will fail. SPEC REQ-08.3 explicitly accepts the
    # data loss — these rows reference a long-gone connector and were never
    # reachable through the portal UI anyway.
    op.execute(
        """
        DELETE FROM connector.sync_runs
         WHERE connector_id NOT IN (
            SELECT id FROM public.portal_connectors
         )
        """
    )

    # Step 2: add the cross-schema FK with ON DELETE CASCADE.
    # NB: PostgreSQL allows cross-schema FKs as long as the referencing role
    # has REFERENCES on the target table. If you hit a permission error,
    # see the docstring's "Pre-flight" section.
    op.create_foreign_key(
        "fk_sync_runs_portal_connectors",
        source_table="sync_runs",
        referent_table="portal_connectors",
        local_cols=["connector_id"],
        remote_cols=["id"],
        source_schema="connector",
        referent_schema="public",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Restore the pre-migration state: drop the FK. We deliberately do NOT
    # restore the orphan rows we deleted in upgrade() — the data is gone
    # and the original 004 migration also did not preserve it across the
    # FK removal.
    op.drop_constraint(
        "fk_sync_runs_portal_connectors",
        "sync_runs",
        type_="foreignkey",
        schema="connector",
    )
