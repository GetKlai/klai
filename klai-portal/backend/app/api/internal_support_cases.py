"""Internal support-case ingest endpoints (SPEC-RAG-SUPPORT-GAP).

Service-to-service callbacks from the connector's HubSpot support sync. Auth is
the portal inbound bearer secret (``settings.internal_secret``), matching
``internal_connectors.py``. The server derives organization, owner and KB from
the *active connector* — a caller cannot choose another tenant in the body — and
enforces that the payload's ``account_id`` matches the connector's configured
account.

Endpoints:
  - POST /api/internal/connectors/{connector_id}/support-cases
        Upsert one validated case; returns {case_id, status, changed,
        findings_count}. 403 when the org may not hold literal evidence.
  - POST /api/internal/connectors/{connector_id}/reconcile
        Delete cases no longer in the connector's selected scope; only ever
        called after a fully successful snapshot.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.schemas_support_cases import SupportCasePayload
from app.services.access import is_personal_kb
from app.services.support_cases import (
    GAP_FEATURE,
    OversizedCaseError,
    SupportTelemetryError,
    UpsertResult,
    reconcile_support_cases,
    upsert_support_case,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/internal/connectors", tags=["internal"])

_SUPPORT_CONNECTOR_TYPE = "hubspot_support"


class SupportCaseUpsertResponse(BaseModel):
    case_id: int
    status: str
    changed: bool
    findings_count: int


class ReconcileRequest(BaseModel):
    external_ids: list[str]


class ReconcileResponse(BaseModel):
    deleted: int


def _verify_internal_bearer(authorization: str | None) -> None:
    """Constant-time check on ``Authorization: Bearer <token>``.

    ``internal_secret`` is the INBOUND secret for service->portal calls, same as
    ``internal_connectors.finalize_connector_delete``.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    presented = authorization.removeprefix("Bearer ")
    if not hmac.compare_digest(presented, settings.internal_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def _load_active_support_connector(db: AsyncSession, connector_id: str) -> PortalConnector:
    """Load the connector by id (Cat-A, permissive pre-tenant SELECT).

    404 when absent, 409 when not an active ``hubspot_support`` connector — the
    ingest surface must never write cases for the wrong connector type.
    """
    result = await db.execute(select(PortalConnector).where(PortalConnector.id == connector_id))
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    if connector.connector_type != _SUPPORT_CONNECTOR_TYPE or connector.state != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connector {connector_id} is not an active {_SUPPORT_CONNECTOR_TYPE} connector",
        )
    return connector


async def _resolve_org_and_kb(db: AsyncSession, connector: PortalConnector) -> tuple[PortalOrg, PortalKnowledgeBase]:
    """Resolve the connector's org + comparison-scope KB, with tenant context set.

    Rejects a personal KB: only organization-owned KBs may hold support
    evidence initially (contract: "Only organization-owned KBs are supported").
    """
    org = await db.get(PortalOrg, connector.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    await set_tenant(db, connector.org_id)

    kb_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id == connector.kb_id,
            PortalKnowledgeBase.org_id == connector.org_id,
        )
    )
    kb = kb_result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    if is_personal_kb(kb):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Support cases require an organization-owned knowledge base",
        )
    return org, kb


def _enforce_account(connector: PortalConnector, payload_account_id: str) -> None:
    """The payload must target the connector's configured account."""
    configured = (connector.config or {}).get("account_id")
    if configured is None or payload_account_id != configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payload account_id does not match the connector's configured account",
        )


@router.post("/{connector_id}/support-cases", response_model=SupportCaseUpsertResponse)
async def ingest_support_case(
    connector_id: str,
    payload: SupportCasePayload,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> SupportCaseUpsertResponse:
    _verify_internal_bearer(authorization)
    connector = await _load_active_support_connector(db, connector_id)
    org, kb = await _resolve_org_and_kb(db, connector)
    # knowledge_gaps must be unlocked for this tenant before any store/model work.
    # The service re-checks this authoritatively under a lock; rejecting here keeps
    # a disabled tenant's evidence out even when upsert is stubbed/short-circuited.
    assert_platform_unlocked(org, GAP_FEATURE)
    _enforce_account(connector, payload.account_id)

    try:
        result: UpsertResult = await upsert_support_case(
            db,
            org_id=org.id,
            zitadel_org_id=org.zitadel_org_id,
            telemetry_level=org.telemetry_level,
            connector_id=connector.id,
            created_by=connector.created_by,
            kb_slug=kb.slug,
            payload=payload,
        )
    except SupportTelemetryError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        ) from exc
    except OversizedCaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "support_case_too_large"},
        ) from exc

    return SupportCaseUpsertResponse(
        case_id=result.case_id,
        status=result.status,
        changed=result.changed,
        findings_count=result.findings_count,
    )


@router.post("/{connector_id}/support-cases/reconcile", response_model=ReconcileResponse)
async def reconcile_connector_support_cases(
    connector_id: str,
    body: ReconcileRequest,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> ReconcileResponse:
    """Delete cases outside the connector's current selected scope.

    Only a fully successful snapshot may call this (the connector sync gates it
    upstream); a partial read never reaches here, or it would delete live cases
    as phantom deletions.
    """
    _verify_internal_bearer(authorization)
    connector = await _load_active_support_connector(db, connector_id)
    org, _kb = await _resolve_org_and_kb(db, connector)
    # A disabled/partial import may not drive destructive reconciliation. The
    # service re-checks under a lock; this early gate blocks it before the delete.
    assert_platform_unlocked(org, GAP_FEATURE)
    deleted = await reconcile_support_cases(
        db,
        org_id=connector.org_id,
        connector_id=connector.id,
        external_ids=body.external_ids,
    )
    return ReconcileResponse(deleted=deleted)
