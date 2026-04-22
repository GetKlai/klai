"""Tenant provisioning state machine — SPEC-PROV-001 M2.

One-level compensating transaction: each forward step maps to a database
checkpoint on `portal_orgs.provisioning_status`. Transitions are serialised per
org via `SELECT ... FOR UPDATE`, logged as structured events, and emitted as
`product_events` rows so ops can build a timeline per tenant signup in Grafana.

The companion `AsyncExitStack`-driven rollback lives in `orchestrator.py` — this
module is intentionally free of side-effects against external services so it is
trivial to reason about and to unit test.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal import PortalOrg
from app.services.events import emit_event

logger = structlog.get_logger()


class StateTransitionConflict(RuntimeError):
    """Raised when the expected `from_state` does not match the row's current state.

    This typically signals a concurrent provisioning or retry attempt — the caller
    should treat it as a 409 Conflict and abort, not retry.
    """


# Ordered sequence of forward steps as defined in SPEC-PROV-001 §R8. Each tuple is
# (step_name, next_state). `step_name` is used as the `step` kwarg in structured
# logs and as the `properties.step` field in product_events. `next_state` is the
# checkpoint written to `portal_orgs.provisioning_status` immediately before the
# step runs.
#
# @MX:ANCHOR: SPEC-PROV-001 R8 — this sequence IS the state machine. Changing
# order or states here requires a matching Alembic CHECK constraint update AND a
# compensator review in orchestrator.py.
FORWARD_SEQUENCE: Final[list[tuple[str, str]]] = [
    ("zitadel_oidc_app", "creating_zitadel_app"),
    ("litellm_team", "creating_litellm_team"),
    ("mongo_user", "creating_mongo_user"),
    ("env_file", "writing_env_file"),
    ("personal_kb", "creating_personal_kb"),
    ("portal_kbs", "creating_portal_kbs"),
    ("librechat_container", "starting_container"),
    ("tenant_caddyfile", "writing_caddyfile"),
    ("caddy_reload", "reloading_caddy"),
    ("system_groups", "creating_system_groups"),
]

# Terminal states that indicate the provisioning run is finished (successfully or
# otherwise). Callers can use this set to gate retry/observability logic.
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"ready", "failed_rollback_complete", "failed_rollback_pending"})

# States that the startup stuck-detector (M7) should actively reconcile when they
# persist past the detector's `updated_at` grace period. These are all states
# except the terminal ones, `pending` (signup transient), and `queued` (awaiting
# BackgroundTask start). Changing this set requires updating M7 tests.
STUCK_CANDIDATE_STATES: Final[frozenset[str]] = frozenset({next_state for _, next_state in FORWARD_SEQUENCE})

# Start timestamps per (org_id, step) for duration_ms measurement. Scoped to a
# single provisioning run; the orchestrator resets this per run.
_step_start_times: dict[tuple[int, str], float] = {}


def mark_step_start(org_id: int, step: str) -> None:
    """Record the wall-clock start of a step for later duration_ms measurement.

    Called by the orchestrator immediately before `transition_state`. Safe to
    call multiple times for the same (org_id, step) — later calls overwrite.
    """
    _step_start_times[(org_id, step)] = time.monotonic()


def _consume_duration_ms(org_id: int, step: str) -> int | None:
    """Return elapsed ms since `mark_step_start` for this (org_id, step), if any."""
    start = _step_start_times.pop((org_id, step), None)
    if start is None:
        return None
    return int((time.monotonic() - start) * 1000)


async def transition_state(
    db: AsyncSession,
    org_id: int,
    *,
    from_state: str | None,
    to_state: str,
    step: str,
) -> None:
    """Atomically transition `portal_orgs.provisioning_status` to `to_state`.

    Acquires a row-level lock via `SELECT ... FOR UPDATE`, verifies the current
    state matches `from_state` (if provided), writes the new state, commits,
    and emits exactly one structured log entry plus one product_event.

    Args:
        db: The async SQLAlchemy session used for the whole provisioning run.
        org_id: Primary key of the `portal_orgs` row.
        from_state: Expected current `provisioning_status`. If ``None`` the
            lock is taken without precondition (used for the initial
            `pending`/`queued` → first step transition where both legacy and
            new entry values must be accepted).
        to_state: New `provisioning_status` value. Must be one of the values
            enumerated in the CHECK constraint `ck_portal_orgs_provisioning_status`.
        step: Human-readable step name (see `FORWARD_SEQUENCE`), included in
            structured logs and product_event properties.

    Raises:
        StateTransitionConflict: If `from_state` is provided and the row's
            current `provisioning_status` does not match — typically a
            concurrent run.
        LookupError: If no row with `org_id` exists.
    """
    # @MX:NOTE: SPEC-PROV-001 R2/R14 — FOR UPDATE is the concurrency guarantee.
    # Do not remove the with_for_update() call without an alternative lock.
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
    org = result.scalar_one_or_none()
    if org is None:
        raise LookupError(f"portal_orgs row not found for org_id={org_id}")

    current_state = org.provisioning_status

    if from_state is not None:
        # R5 allows the initial transition from either `pending` or `queued`,
        # which is handled by passing from_state=None from the orchestrator for
        # that first step; for every subsequent step we expect an exact match.
        if current_state != from_state:
            raise StateTransitionConflict(
                f"org_id={org_id} expected from_state={from_state!r} but found {current_state!r}"
            )

    org.provisioning_status = to_state
    await db.commit()

    duration_ms = _consume_duration_ms(org_id, step)

    logger.info(
        "provisioning_state_transition",
        org_id=org_id,
        slug=org.slug,
        from_state=current_state,
        to_state=to_state,
        step=step,
        duration_ms=duration_ms,
    )

    # Fire-and-forget product_event — failures inside emit_event are logged at
    # WARNING level by the helper and never block the provisioning run.
    emit_event(
        event_type="provisioning.state_transition",
        org_id=org_id,
        user_id=None,
        properties={
            "from_state": current_state,
            "to_state": to_state,
            "step": step,
            "duration_ms": duration_ms,
        },
    )
