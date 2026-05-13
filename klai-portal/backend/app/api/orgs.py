"""SPEC-PRIVACY-QUERY-SHADOW-001 REQ-15 — tenant self-service endpoints.

Tenant-admin scoped endpoints for org-level settings the tenant can flip
without an operator round-trip. Currently only the telemetry-level
toggle (the privacy posture vs debug capability decision is owned by
the tenant, not Klai).

Auth contract: ``Depends(get_caller_at_least(ProfileRole.ADMIN))`` resolves
the OIDC subject to a ``UserPermissions`` and raises 403 if the caller is
not an org admin. The shared service-layer ``set_telemetry_level``
guarantees identical DB behaviour to the operator-side endpoint
(REQ-11) — single audit-log + cache-invalidation path, only the
``operator_kind`` field differs.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller_at_least
from app.core.profiles import ProfileRole
from app.services.telemetry_level import set_telemetry_level

logger = logging.getLogger(__name__)

# /api/orgs/me/* — tenant-self-service surface. The "me" suffix mirrors
# /api/me's user-self-service convention; "orgs/me" reads as "the
# org of the calling user". The handler refuses any org_id that doesn't
# match the caller's resolved org (defense-in-depth).
router = APIRouter(prefix="/api/orgs", tags=["orgs"])


class TelemetryLevelUpdate(BaseModel):
    level: Literal["off", "shadow", "full"]


class TelemetryLevelOut(BaseModel):
    telemetry_level: Literal["off", "shadow", "full"]


@router.post("/me/telemetry-level", response_model=TelemetryLevelOut)
async def set_my_org_telemetry_level(
    body: TelemetryLevelUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> TelemetryLevelOut:
    """Tenant-admin endpoint to flip their own org's telemetry mode.

    REQ-15 contract:
    - Caller MUST hold the ``admin`` role on the resolved org (else 403)
    - DB update is scoped to ``perms.org_id`` — cross-org attempts via
      a manipulated path are impossible because the org is read from
      the caller's JWT, not the URL
    - Audit row records ``operator_kind='tenant_admin'``,
      ``reason='tenant self-service via admin UI'``,
      ``operator_user_id=<zitadel sub>``
    - Cache invalidation runs so the next chat completion picks up the
      new level within ~30s (kb_ver Redis pointer expiry)
    """
    try:
        _, new_level = await set_telemetry_level(
            db,
            org_id=perms.org_id,
            new_level=body.level,
            operator_kind="tenant_admin",
            operator_user_id=perms.user_id,
            reason="tenant self-service via admin UI",
        )
    except LookupError as exc:
        # Should never happen — get_caller_at_least just returned a perms
        # for this org. Preserved for defense-in-depth against a race with
        # another operation deleting the org row.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        # pydantic Literal already restricts the input; this is the
        # service-layer's reason validation.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return TelemetryLevelOut(telemetry_level=new_level)
