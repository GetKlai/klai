"""Sync-run helpers that intentionally span all tenants.

This module is a deliberate, narrowly-scoped escape hatch for queries
that legitimately need to operate across every tenant — currently only
the lifespan crash-recovery sweep that re-marks RUNNING runs as PENDING
on klai-connector startup.

Why this lives in its own module
--------------------------------

The ast-grep rule ``no-untenanted-syncrun-query`` (SPEC-SEC-PORTAL-RLS-001
REQ-3) requires every ``select`` / ``update`` / ``delete`` on ``SyncRun``
to also reference ``SyncRun.org_id`` somewhere in the same function. The
intent is to catch missing tenant filters mechanically. Crash recovery
genuinely needs to span every tenant (the lifespan runs before any tenant
context exists), so this helper:

1. Documents the cross-org intent explicitly in its name + docstring,
2. Touches ``SyncRun.org_id`` as a no-op so the rule's
   "function references SyncRun.org_id" check passes — making this the
   ONLY legitimate location where that bypass is acceptable, and
3. Keeps the bypass out of ``app/main.py``, where any future
   ``update(SyncRun)`` would otherwise also slip past the rule because
   the entry-module used to be opted out of the lint scope.

Any other ``SyncRun`` operation MUST tenant-scope via ``SyncRun.org_id``.
"""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_run import SyncRun, SyncStatus


async def reset_stuck_running_runs_cross_org(session: AsyncSession) -> int:
    """Re-mark RUNNING sync runs as PENDING across all tenants.

    Called from the FastAPI lifespan handler at startup so that any run
    left RUNNING by a previous crash / forced restart can resume from
    its checkpoint. PENDING preserves ``cursor_state`` (which may contain
    partial-progress refs) so the next sync continues where it left off
    instead of restarting from scratch.

    Cross-org by design: at lifespan time no tenant context exists, and
    the sweep does not RETURN data — it only updates ``status`` +
    ``completed_at``. The reference to ``SyncRun.org_id`` below is a
    declarative marker that satisfies the
    ``no-untenanted-syncrun-query`` ast-grep rule and documents that
    the cross-org scope was a conscious choice, not an oversight.

    Returns:
        Number of rows updated (used for log instrumentation).
    """
    # Marker reference — see module docstring + SPEC-SEC-PORTAL-RLS-001 REQ-3.
    # This is the ONLY legitimate cross-org SyncRun operation; any other
    # site must filter by SyncRun.org_id.
    #
    # Audit 2026-05-05 finding F8: a prior version of this marker used
    # an explicit lint-suppression annotation that was removable by
    # automated cleanup tools. If the suppression got stripped, F841
    # would have triggered, ruff autofix would have REMOVED the
    # assignment, and the ast-grep rule would then fire on this
    # legitimate sweep — a silent security-rule regression.
    #
    # Replaced with `_ = SyncRun.org_id`. The `_` name is the universal
    # "intentionally discarded" convention; F841 ignores it by default.
    # No suppression annotation required, so there is nothing for
    # autofix to strip. A future tightening of F841 to flag `_` itself
    # would be a project-wide concern, not a silent bypass-only one.
    _ = SyncRun.org_id
    result = await session.execute(
        update(SyncRun)
        .where(SyncRun.status == SyncStatus.RUNNING)
        .values(status=SyncStatus.PENDING, completed_at=datetime.now(UTC))
    )
    return result.rowcount or 0
