"""SPEC-PROV-001 M7 — startup reconciliation for stuck provisioning runs.

When portal-api crashes (OOM, SIGKILL, deploy) midway through a provisioning
BackgroundTask, the in-memory `AsyncExitStack` is lost and the org row stays on
its last-written intermediate state (e.g. `creating_mongo_user`). The retry
endpoint refuses to touch these rows because blindly re-running forward steps
on top of half-created external resources would double-book them.

This module runs once at startup: it finds every org stuck in an intermediate
state for longer than a configurable grace period and transitions it to
`failed_rollback_pending` so it becomes visible in Grafana and ops can inspect
the external resources manually.

Crucially, the detector does NOT run compensators. The in-memory _ProvisionState
that would tell us which resources to clean up is gone. Automated cleanup
without that context could delete resources that belong to a different,
still-healthy tenant.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal import PortalOrg
from app.services.events import emit_event
from app.services.provisioning.state_machine import (
    STUCK_CANDIDATE_STATES,
    StateTransitionConflict,
    transition_state,
)

logger = structlog.get_logger()

# Grace period: a provisioning run that has written a state transition in the
# last N minutes is considered alive; older rows in an intermediate state are
# candidates for reconciliation. The default is generous (provisioning should
# never take longer than ~5 minutes) so in-flight runs are never disrupted.
DEFAULT_STUCK_THRESHOLD = timedelta(minutes=15)


async def reconcile_stuck_provisionings(
    db: AsyncSession,
    *,
    threshold: timedelta = DEFAULT_STUCK_THRESHOLD,
) -> int:
    """Find and reconcile provisioning rows stuck in intermediate states.

    Args:
        db: Async SQLAlchemy session.
        threshold: A row is considered stuck when
            `updated_at < now() - threshold`. Defaults to 15 minutes.

    Returns:
        Number of rows that were transitioned to `failed_rollback_pending`.
    """
    cutoff_interval = func.make_interval(0, 0, 0, 0, 0, int(threshold.total_seconds() // 60))

    # Candidate rows: intermediate state AND last update was before the cutoff.
    stuck_result = await db.execute(
        select(PortalOrg.id, PortalOrg.slug, PortalOrg.provisioning_status, PortalOrg.updated_at).where(
            PortalOrg.provisioning_status.in_(STUCK_CANDIDATE_STATES),
            PortalOrg.updated_at < func.now() - cutoff_interval,
        )
    )
    stuck_rows = stuck_result.all()

    if not stuck_rows:
        return 0

    reconciled = 0
    for row in stuck_rows:
        try:
            await transition_state(
                db,
                row.id,
                from_state=row.provisioning_status,
                to_state="failed_rollback_pending",
                step="stuck_reconciliation",
            )
        except StateTransitionConflict:
            # A concurrent actor (very unlikely at startup, but possible in a
            # multi-replica future) moved this row between our SELECT and the
            # FOR UPDATE. Skip and let the next startup reconcile if needed.
            logger.warning(
                "stuck_reconciliation_skipped_conflict",
                org_id=row.id,
                slug=row.slug,
            )
            continue
        except Exception:
            logger.exception(
                "stuck_reconciliation_failed",
                org_id=row.id,
                slug=row.slug,
                last_state=row.provisioning_status,
            )
            continue

        logger.warning(
            "provisioning_stuck_detected",
            org_id=row.id,
            slug=row.slug,
            last_state=row.provisioning_status,
            stuck_since=row.updated_at.isoformat() if row.updated_at else None,
        )
        emit_event(
            event_type="provisioning.stuck_recovered",
            org_id=row.id,
            user_id=None,
            properties={
                "last_state": row.provisioning_status,
                "stuck_since": row.updated_at.isoformat() if row.updated_at else None,
                "threshold_seconds": int(threshold.total_seconds()),
            },
        )
        reconciled += 1

    return reconciled
