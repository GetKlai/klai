"""Internal portal-to-connector endpoints.

These routes are protected by the portal_caller_secret bypass in
``AuthMiddleware`` (X-Internal-Secret header validation). No Zitadel
OIDC introspection is performed; the caller must hold the shared secret.

Currently registered routes:

    POST /internal/v1/orgs/{org_id}/wipe-state
        SPEC-INFRA-TENANT-DELETE-002 G6 — purge connector.sync_runs for
        an org. Part of the tenant wipe orchestration.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.logging import get_logger
from app.models.sync_run import SyncRun

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class WipeStateResponse(BaseModel):
    """Response body for the wipe-state endpoint."""

    rows_deleted: int
    status: str


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _require_portal_call(request: Request) -> None:
    """Raise 403 if the request did not arrive via the portal internal secret.

    ``AuthMiddleware`` sets ``request.state.from_portal = True`` when the
    ``Authorization: Bearer <portal_caller_secret>`` header matches. Any
    other authenticated caller (Zitadel OIDC) is rejected here.
    """
    if not getattr(request.state, "from_portal", False):
        raise HTTPException(status_code=403, detail="Portal service token required")


# ---------------------------------------------------------------------------
# Wipe endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/orgs/{org_id}/wipe-state",
    response_model=WipeStateResponse,
    status_code=200,
)
async def wipe_org_state(
    org_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WipeStateResponse:
    """Purge all ``connector.sync_runs`` rows for the given org_id.

    SPEC-INFRA-TENANT-DELETE-002 G6 — tenant wipe orchestration, step 8a.

    Design decisions:
    - Idempotent: calling this endpoint when no rows match returns
      ``rows_deleted=0`` with HTTP 200. Safe to retry.
    - NULL-org rows (legacy rows that pre-date SPEC-SEC-TENANT-001 REQ-7.2
      migration 006) are intentionally NOT touched. Those rows have
      ``org_id IS NULL`` and do not match ``WHERE org_id = :org_id``.
      Separate cleanup tooling handles them if ever needed.
    - Auth: relies entirely on ``AuthMiddleware`` + ``_require_portal_call``.
      No inline secret comparison here — auth is the middleware's job.
    - Scope: this endpoint purges sync_run history only. Connector config
      rows (``connector.connectors``) are NOT deleted here; the portal
      orchestrator handles those via the existing connector-delete lifecycle.
    """
    _require_portal_call(request)

    logger.info(
        "wipe_org_state_requested",
        extra={"event": "wipe_org_state_requested", "org_id": org_id},
    )

    stmt = delete(SyncRun).where(SyncRun.org_id == org_id)
    result = await session.execute(stmt)
    await session.commit()

    rows_deleted: int = result.rowcount if result.rowcount is not None else 0

    logger.info(
        "wipe_org_state_completed",
        extra={
            "event": "wipe_org_state_completed",
            "org_id": org_id,
            "rows_deleted": rows_deleted,
        },
    )

    return WipeStateResponse(rows_deleted=rows_deleted, status="ok")
