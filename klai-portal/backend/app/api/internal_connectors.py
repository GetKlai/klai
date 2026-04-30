"""Internal connector lifecycle endpoints.

Service-to-service callbacks from knowledge-ingest's procrastinate worker.
Auth via X-Internal-Secret bearer (matching ``settings.knowledge_ingest_secret``).
NOT mounted under the user-facing ``/api/app/...`` router family — this is
an internal control-plane surface only.

Currently exposes:
  - POST /api/internal/connectors/{connector_id}/finalize-delete
        Hard-delete a connector that has been sitting in
        ``state='deleting'``. Idempotent: a connector that is already gone
        (or was never in 'deleting') returns 204 too. Caller is the
        ``connector_purge_task`` from knowledge-ingest after
        ``connector_cleanup.purge_connector`` has finished.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-04.4.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.connectors import PortalConnector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/connectors", tags=["internal"])


def _verify_internal_bearer(authorization: str | None) -> None:
    """Constant-time check on the Authorization: Bearer <token> header.

    Mirrors the pattern in other internal endpoints (taxonomy/internal,
    docs/internal). Fails 401 on missing or mismatched.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    presented = authorization.removeprefix("Bearer ")
    if not hmac.compare_digest(presented, settings.knowledge_ingest_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.post(
    "/{connector_id}/finalize-delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def finalize_connector_delete(
    connector_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hard-delete a connector that the purge worker has finished cleaning.

    REQ-04.4. Auth: shared internal secret (knowledge-ingest only).

    Idempotent:
      - row exists with state='deleting' -> DELETE, return 204.
      - row exists with state='active'    -> 409 (worker shouldn't call
        on an active row; surface the bug instead of silently dropping).
      - row absent                        -> return 204 (already gone,
        worker is retrying after a partial success).
    """
    _verify_internal_bearer(authorization)

    result = await db.execute(select(PortalConnector).where(PortalConnector.id == connector_id))
    connector = result.scalar_one_or_none()
    if connector is None:
        # Already hard-deleted; idempotent.
        logger.info(
            "finalize_connector_delete_already_gone",
            extra={"connector_id": connector_id},
        )
        return None

    if connector.state != "deleting":
        # Worker bug or stale message: refuse to drop an active row.
        logger.error(
            "finalize_connector_delete_invalid_state",
            extra={"connector_id": connector_id, "state": connector.state},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connector is in state {connector.state!r}, expected 'deleting'",
        )

    await db.execute(delete(PortalConnector).where(PortalConnector.id == connector_id))
    await db.commit()
    logger.info(
        "finalize_connector_delete_completed",
        extra={"connector_id": connector_id},
    )
    return None
