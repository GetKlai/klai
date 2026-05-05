"""Internal portal-to-connector endpoints.

These routes are protected by the portal_caller_secret bypass in
``AuthMiddleware`` (X-Internal-Secret header validation). No Zitadel
OIDC introspection is performed; the caller must hold the shared secret.

Currently registered routes:

    POST /internal/v1/orgs/{org_id}/wipe-state
        SPEC-INFRA-TENANT-DELETE-002 G6 — purge connector.sync_runs for
        an org. Part of the tenant wipe orchestration.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.logging import get_logger
from app.models.connector import Connector
from app.models.sync_run import SyncRun
from app.routes.sync import _require_portal_call  # pyright: ignore[reportPrivateUsage]

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class WipeStateResponse(BaseModel):
    """Response body for the wipe-state endpoint.

    ``rows_deleted`` is the total across both tables; ``per_table`` breaks
    it out for audit-log visibility.
    """

    rows_deleted: int
    per_table: dict[str, int]
    status: str


# ---------------------------------------------------------------------------
# Note: ``_require_portal_call`` is the canonical auth helper defined in
# ``app.routes.sync``. We import it above (with a pyright ignore for the
# leading underscore — it's effectively semi-public within the connector
# routes layer). Same pattern as ``app.routes.fingerprint``. Re-defining
# it here would create a maintenance hazard if the auth contract ever
# changes (one site updated, the other forgotten = silent drift).
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
    """Purge ALL connector-schema rows for the given org_id.

    SPEC-INFRA-TENANT-DELETE-002 G6 — tenant wipe orchestration, step 9b.

    Wipes both tenant-scoped tables in the ``connector`` schema in a single
    transaction:

    1. ``connector.sync_runs`` — sync history rows (audit log of which
       runs happened, with status / cursor_state / error_details). Has an
       FK to ``connector.connectors`` with ``ON DELETE CASCADE``, so this
       MUST be deleted first OR cascade from the connectors DELETE. We
       delete sync_runs explicitly first so the per-table count surfaces
       in the response (the orchestrator can audit how many sync_runs
       were purged for the deprovisioned tenant).

    2. ``connector.connectors`` — connector config rows. Each row holds
       the per-tenant adapter config + (encrypted) credentials in
       ``portal_secret_id``. Without this DELETE, deprovisioned tenants'
       OAuth tokens / API keys remain at rest in the connector DB.

    Design decisions:
    - Idempotent: calling this endpoint when no rows match returns
      ``rows_deleted=0`` with HTTP 200. Safe to retry.
    - NULL-org rows (legacy rows that pre-date SPEC-SEC-TENANT-001 REQ-7.2
      migration 006) are intentionally NOT touched. Those rows have
      ``org_id IS NULL`` and do not match ``WHERE org_id = :org_id``.
      Separate cleanup tooling handles them if ever needed.
    - Auth: relies entirely on ``AuthMiddleware`` + ``_require_portal_call``.
      No inline secret comparison here — auth is the middleware's job.
    - Single transaction: both DELETEs commit together. If the second
      fails, the first rolls back (no half-purged state).
    """
    _require_portal_call(request)

    logger.info(
        "wipe_org_state_requested",
        extra={"event": "wipe_org_state_requested", "org_id": org_id},
    )

    # Delete sync_runs first (FK child), then connectors (FK parent).
    sync_runs_result = await session.execute(
        delete(SyncRun).where(SyncRun.org_id == org_id)
    )
    connectors_result = await session.execute(
        delete(Connector).where(Connector.org_id == org_id)
    )
    await session.commit()

    sync_runs_deleted: int = sync_runs_result.rowcount if sync_runs_result.rowcount is not None else 0
    connectors_deleted: int = connectors_result.rowcount if connectors_result.rowcount is not None else 0
    rows_deleted: int = sync_runs_deleted + connectors_deleted

    logger.info(
        "wipe_org_state_completed",
        extra={
            "event": "wipe_org_state_completed",
            "org_id": org_id,
            "rows_deleted": rows_deleted,
            "sync_runs_deleted": sync_runs_deleted,
            "connectors_deleted": connectors_deleted,
        },
    )

    return WipeStateResponse(
        rows_deleted=rows_deleted,
        per_table={"sync_runs": sync_runs_deleted, "connectors": connectors_deleted},
        status="ok",
    )
