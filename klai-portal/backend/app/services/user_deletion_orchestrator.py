"""State-machine orchestrator for platform user hard-delete.

Mirrors the structure of deprovisioning_orchestrator.py.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-4 (Finding A-2, HIGH)

The delete sequence is:
  1. step_zitadel_remove       — remove Zitadel identity
  2. step_external_kb_delete   — purge KBs + revoke credentials
  3. step_portal_db_delete     — DELETE portal_users + audit (same tx)

On any step failure:
  - Write portal_users.deletion_status = 'failed_partial'
  - Write portal_users.failure_reason JSONB
  - Write portal_users.last_attempted_step TEXT
  - Emit audit event platform_admin.user_delete_partial_failure

# @MX:ANCHOR fan_in=2
# @MX:NOTE: Steps execute sequentially; there is no retry per step (unlike
#   deprovisioning_orchestrator). The retry endpoint (platform_retry_user_delete)
#   restarts the full sequence from scratch, which is idempotent per step.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.user_deletion_steps as _steps
from app.services.audit import log_event

logger = structlog.get_logger()

_STEP_SEQUENCE = [
    "step_zitadel_remove",
    "step_external_kb_delete",
    "step_portal_db_delete",
]

_STEP_DISPLAY_NAMES = {
    "step_zitadel_remove": "zitadel_remove",
    "step_external_kb_delete": "external_kb_delete",
    "step_portal_db_delete": "portal_db_delete",
}


def _get_steps():
    """Return (step_fn, step_display_name) pairs, resolved at call-time."""
    return [(getattr(_steps, attr), _STEP_DISPLAY_NAMES[attr]) for attr in _STEP_SEQUENCE]


@dataclass
class _UserDeletionState:
    """Mutable state passed through each step function."""

    org_id: int
    zitadel_user_id: str
    actor_user_id: str
    delete_global_identity: bool

    # Pre-computed by the request handler before calling delete_user_with_state_machine.
    kb_dispositions: list[Any] = field(default_factory=list)
    api_keys_count: int = 0
    mcp_tokens_count: int = 0

    # Resolved by load_user_deletion_state from DB
    org: Any = None
    portal_user: Any = None

    # Session for DB operations inside steps (tenant-scoped, open during run).
    db_for_steps: AsyncSession | None = None

    # Progress flags updated as steps complete.
    zitadel_identity_deleted: bool = False
    kbs_deleted_externally: int = 0
    db_user_deleted: bool = False


async def _mark_user_delete_failed(
    state: _UserDeletionState,
    db: AsyncSession,
    step_name: str,
    error_str: str,
) -> None:
    """Write deletion_status='failed_partial', failure_reason, last_attempted_step.

    Best-effort: exceptions here are logged but not re-raised.
    Uses raw text() to avoid RLS complications on the portal_users UPDATE.
    """
    try:
        failed_at = datetime.now(UTC).isoformat()
        failure_payload = json.dumps(
            {
                "step": step_name,
                "error": error_str[:500],
                "failed_at": failed_at,
            }
        )
        await db.execute(
            text(
                "UPDATE portal_users"
                " SET deletion_status = 'failed_partial',"
                "     failure_reason = CAST(:failure AS jsonb),"
                "     last_attempted_step = :step"
                " WHERE zitadel_user_id = :uid AND org_id = :org_id"
            ),
            {
                "failure": failure_payload,
                "step": step_name,
                "uid": state.zitadel_user_id,
                "org_id": state.org_id,
            },
        )
        await db.commit()
        logger.info(
            "user_deletion.failed_partial_state_set",
            zitadel_user_id=state.zitadel_user_id,
            step=step_name,
        )
    except Exception:
        logger.exception(
            "user_deletion.mark_failed_error",
            zitadel_user_id=state.zitadel_user_id,
            step=step_name,
        )
        with suppress(Exception):
            await db.rollback()


async def _run_user_deletion(
    state: _UserDeletionState,
    db: AsyncSession,
) -> None:
    """Execute the three-step deletion sequence.

    On success: emits platform_admin.user_deleted audit event.
    On failure: calls _mark_user_delete_failed + emits
    platform_admin.user_delete_partial_failure.

    Does NOT raise — the caller (endpoint handler) inspects state flags.
    """
    state.db_for_steps = db

    for step_fn, step_name in _get_steps():
        try:
            await step_fn(state)
        except Exception as exc:
            # Record failure state in DB.
            await _mark_user_delete_failed(state, db, step_name, str(exc))

            # Emit permanent audit event.
            with suppress(Exception):
                await log_event(
                    org_id=state.org_id,
                    actor=state.actor_user_id,
                    action="platform_admin.user_delete_partial_failure",
                    resource_type="user",
                    resource_id=state.zitadel_user_id,
                    details={
                        "step": step_name,
                        "error": str(exc)[:200],
                        "kbs_deleted_externally": state.kbs_deleted_externally,
                        "api_keys_revoked": state.api_keys_count,
                        "mcp_tokens_revoked": state.mcp_tokens_count,
                        "zitadel_identity_deleted": state.zitadel_identity_deleted,
                        "db_user_deleted": state.db_user_deleted,
                    },
                )
            return

    # All three steps succeeded — emit success audit event.
    await log_event(
        org_id=state.org_id,
        actor=state.actor_user_id,
        action="platform_admin.user_deleted",
        resource_type="user",
        resource_id=state.zitadel_user_id,
        details={
            "target_org_id": state.org_id,
            "kbs_deleted": state.kbs_deleted_externally,
            "api_keys_revoked": state.api_keys_count,
            "mcp_tokens_revoked": state.mcp_tokens_count,
            "global_identity_deleted": state.zitadel_identity_deleted,
            "zitadel_identity_deleted": state.zitadel_identity_deleted,
            "db_user_deleted": state.db_user_deleted,
        },
    )


async def delete_user_with_state_machine(
    *,
    org_id: int,
    zitadel_user_id: str,
    actor_user_id: str,
    delete_global_identity: bool,
    kb_dispositions: list[Any],
    api_keys_count: int,
    mcp_tokens_count: int,
    org: Any,
    portal_user: Any,
    db: AsyncSession,
) -> bool:
    """Entry point called by the endpoint handler.

    Returns True if all steps succeeded, False on partial failure.
    The caller should check this to decide the response message.
    """
    state = _UserDeletionState(
        org_id=org_id,
        zitadel_user_id=zitadel_user_id,
        actor_user_id=actor_user_id,
        delete_global_identity=delete_global_identity,
        kb_dispositions=kb_dispositions,
        api_keys_count=api_keys_count,
        mcp_tokens_count=mcp_tokens_count,
        org=org,
        portal_user=portal_user,
    )
    await _run_user_deletion(state, db)
    return state.db_user_deleted
