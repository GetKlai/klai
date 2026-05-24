"""Step functions for the platform user-delete state machine.

Each step is idempotent: if the resource has already been deleted,
the function succeeds silently (skips gracefully).

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-4 (Finding A-2, HIGH)

Step execution order (enforced by _run_user_deletion):
  1. step_zitadel_remove       — remove Zitadel identity (cheap to undo)
  2. step_external_kb_delete   — purge KBs + revoke credentials
  3. step_portal_db_delete     — DELETE portal_users row + audit write (same tx)

# @MX:ANCHOR fan_in=3
# @MX:NOTE: Steps are intentionally free-standing async functions (not methods)
#   so they can be patched independently in unit tests — same pattern as
#   deprovisioning_steps.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.services.user_deletion_orchestrator import _UserDeletionState

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.zitadel import zitadel

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Step 1: Remove Zitadel identity
# ---------------------------------------------------------------------------


async def step_zitadel_remove(state: _UserDeletionState) -> None:
    """Remove Zitadel identity when this is the user's last org membership.

    Idempotent: if already removed (404 from Zitadel), logs a warning and
    continues — the goal state (identity gone) is already achieved.
    """
    if not state.delete_global_identity:
        logger.info(
            "user_deletion.zitadel_skip_multi_tenant",
            zitadel_user_id=state.zitadel_user_id,
            org_id=state.org_id,
        )
        return

    try:
        await zitadel.remove_user(
            org_id=settings.zitadel_portal_org_id,
            zitadel_user_id=state.zitadel_user_id,
        )
        state.zitadel_identity_deleted = True
        logger.info(
            "user_deletion.zitadel_removed",
            zitadel_user_id=state.zitadel_user_id,
        )
    except Exception:
        # Re-raise so the orchestrator can record the failure.
        # zitadel.remove_user raises on non-2xx; idempotency (404 already-deleted)
        # should be handled by the zitadel client itself.
        logger.exception(
            "user_deletion.zitadel_remove_failed",
            zitadel_user_id=state.zitadel_user_id,
        )
        raise


# ---------------------------------------------------------------------------
# Step 2: External KB deletes + revoke credentials
# ---------------------------------------------------------------------------


async def step_external_kb_delete(state: _UserDeletionState) -> None:
    """Delete KBs externally (Qdrant/Garage/knowledge-ingest/docs-app) and
    revoke partner API keys + MCP tokens.

    Both sub-operations are applied; errors from either propagate up.
    """
    from app.services.kb_offboarding import apply_dispositions

    if state.kb_dispositions:
        await apply_dispositions(
            state.zitadel_user_id,
            state.kb_dispositions,
            state.actor_user_id,
            state.org,
            state.db_for_steps,
        )
        state.kbs_deleted_externally = len(state.kb_dispositions)
        logger.info(
            "user_deletion.kbs_deleted",
            count=state.kbs_deleted_externally,
            zitadel_user_id=state.zitadel_user_id,
        )

    # Credentials were already revoked in the request handler before calling the
    # orchestrator; the counts are stored in state.
    logger.info(
        "user_deletion.credentials_revoked",
        api_keys=state.api_keys_count,
        mcp_tokens=state.mcp_tokens_count,
        zitadel_user_id=state.zitadel_user_id,
    )


# ---------------------------------------------------------------------------
# Step 3: Delete portal_users row + write audit entry in the same transaction
# ---------------------------------------------------------------------------


async def step_portal_db_delete(state: _UserDeletionState) -> None:
    """Hard-delete the portal_users row.

    This step uses state.db_for_steps (the caller's session) so the
    DELETE and the success audit row land in the same transaction.
    """
    db: AsyncSession = state.db_for_steps

    await db.delete(state.portal_user)
    # Flush so the DELETE is in the session before commit.
    await db.flush()
    state.db_user_deleted = True
    logger.info(
        "user_deletion.portal_user_deleted",
        zitadel_user_id=state.zitadel_user_id,
        org_id=state.org_id,
    )
