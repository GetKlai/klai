"""Tenant lifecycle audit events — SPEC-INFRA-TENANT-DELETE-001 R6.

Append-only audit table that survives the hard-delete of a `portal_orgs` row.
No foreign key to `portal_orgs` by design — the snapshot fields preserve the
tenant identity (id, slug, name) at the moment of the lifecycle event so audit
queries 6 months later can still answer "what was tenant X called and when did
it leave?".

For MVP only `deprovisioned` and `failed_deprovisioning` events are emitted by
the deprovisioning orchestrator. The schema is forward-compatible with
`provisioned` (out-of-scope for this SPEC; will be added to `provision_tenant`
finalizer in a future change).

RLS policy is added in `alembic/versions/post_deploy_*.sql` — portal_api lacks
the role to ALTER TABLE ENABLE ROW LEVEL SECURITY (see portal-security.md).
"""

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantLifecycleEvent(Base):
    __tablename__ = "tenant_lifecycle_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('provisioned', 'deprovisioned', 'failed_deprovisioning')",
            name="ck_tenant_lifecycle_events_event_type",
        ),
        CheckConstraint(
            "actor_type IN ('owner', 'platform_admin', 'system')",
            name="ck_tenant_lifecycle_events_actor_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[Literal["provisioned", "deprovisioned", "failed_deprovisioning"]] = mapped_column(
        Text, nullable=False
    )
    # @MX:NOTE: snapshot fields — no FK to portal_orgs so the row survives hard-delete.
    org_id_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    org_slug_snapshot: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    org_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_type: Mapped[Literal["owner", "platform_admin", "system"]] = mapped_column(Text, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
