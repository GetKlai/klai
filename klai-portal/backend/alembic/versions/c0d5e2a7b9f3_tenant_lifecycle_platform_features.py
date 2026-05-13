"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 — allow 'platform_features_updated' in tenant_lifecycle_events.

The CHECK constraint ``ck_tenant_lifecycle_events_event_type`` on
``tenant_lifecycle_events`` originally listed only the three tenant-lifecycle
events (``provisioned``, ``deprovisioned``, ``failed_deprovisioning``).

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5D added a fourth event type
``platform_features_updated`` for platform-unlock audit, but never updated
the constraint — and the ``/api/admin/orgs/{slug}/platform-unlocks`` PATCH
endpoint that emits the event never had a UI, so the constraint mismatch
went undetected for weeks.

SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4 shipped the new
``/api/admin/extensions`` PATCH which IS used by the frontend, and prod
hit the constraint immediately (asyncpg.CheckViolationError → 500 → user
sees "Opslaan mislukt"). Fixed live on 2026-05-12 via psql as klai
superuser; this migration captures that fix in code so future deploys
reproduce it.

Ownership note: ``tenant_lifecycle_events`` is owned by ``klai`` superuser,
not ``portal_api`` — so the alembic upgrade run by portal-api's entrypoint
CANNOT execute ALTER TABLE on this constraint. The actual DDL lives in
the paired post-deploy SQL file
``post_deploy_c0d5e2a7b9f3_tenant_lifecycle_platform_features.sql``,
applied by an operator via ``scripts/apply_post_deploy_sql.sh`` (or
``psql`` directly as the ``klai`` role). The Python migration is a stamp-only
no-op so the alembic head advances and the post-deploy file is the canonical
record. Pattern mirrors the RLS post-deploy SQL files in this directory.

Revision: c0d5e2a7b9f3
Down-revision: e0ad7c2b1e80
Created: 2026-05-12
"""

from __future__ import annotations

revision = "c0d5e2a7b9f3"
down_revision = "e0ad7c2b1e80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: actual ALTER TABLE lives in the paired post-deploy SQL file
    # (klai superuser required for a klai-owned table). Live prod was fixed
    # on 2026-05-12 via psql; this stamp keeps the alembic graph in sync.
    pass


def downgrade() -> None:
    # No-op for symmetry. Rolling back the event-type allow-list would only
    # be relevant if the application code stopped emitting
    # ``platform_features_updated`` entirely, in which case the constraint
    # tightening can be applied manually via psql.
    pass
