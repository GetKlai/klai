import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.services.moneybird import MoneybirdService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


async def get_moneybird() -> AsyncIterator[MoneybirdService]:
    svc = MoneybirdService(settings)
    try:
        yield svc
    finally:
        await svc.close()


@router.get("/status")
async def billing_status(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await _load_org_or_500(db, perms.org_id)
    return {
        "billing_status": org.billing_status,
    }


@router.get("/invoices")
async def invoice_portal(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _load_org_or_500(db, perms.org_id)

    if not org.moneybird_contact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Moneybird contact linked",
        )

    try:
        portal_url = await moneybird.get_invoice_portal_url(org.moneybird_contact_id)
    except RuntimeError as exc:
        logger.warning("Invoice portal URL fetch failed for org %d: %s", perms.org_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve invoice portal URL: {exc}",
        ) from exc

    return {"portal_url": portal_url}
