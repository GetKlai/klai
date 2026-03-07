from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.moneybird import MoneybirdService
from app.services.zitadel import zitadel

router = APIRouter(prefix="/api/billing", tags=["billing"])
bearer = HTTPBearer()


async def get_moneybird() -> AsyncIterator[MoneybirdService]:
    svc = MoneybirdService(settings)
    try:
        yield svc
    finally:
        await svc.close()


async def _get_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> PortalOrg:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc

    org_id: str | None = info.get("urn:zitadel:iam:user:resourceowner:id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Geen organisatie gekoppeld aan dit account",
        )

    result = await db.execute(
        select(PortalOrg).where(PortalOrg.zitadel_org_id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisatie niet gevonden",
        )
    return org


@router.post("/mandate")
async def create_mandate(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _get_org(credentials, db)

    if not org.moneybird_contact_id:
        try:
            contact = await moneybird.create_contact(org.name)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Kon Moneybird contact niet aanmaken: {exc}",
            ) from exc
        org.moneybird_contact_id = str(contact["id"])
        await db.commit()

    try:
        mandate_url = await moneybird.get_mandate_url(org.moneybird_contact_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon mandate URL niet ophalen: {exc}",
        ) from exc

    return {"mandate_url": mandate_url}


@router.get("/status")
async def billing_status(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await _get_org(credentials, db)
    return {
        "billing_status": org.billing_status,
        "moneybird_contact_id": org.moneybird_contact_id,
    }


@router.get("/invoices")
async def invoice_portal(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _get_org(credentials, db)

    if not org.moneybird_contact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Geen Moneybird contact gekoppeld",
        )

    try:
        portal_url = await moneybird.get_invoice_portal_url(org.moneybird_contact_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon factuurportaal URL niet ophalen: {exc}",
        ) from exc

    return {"portal_url": portal_url}


class CancelRequest(BaseModel):
    subscription_id: str


@router.post("/cancel")
async def cancel_subscription(
    body: CancelRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _get_org(credentials, db)

    try:
        await moneybird.cancel_subscription(body.subscription_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon abonnement niet opzeggen: {exc}",
        ) from exc

    org.billing_status = "cancelled"
    await db.commit()

    return {"status": "cancelled"}
