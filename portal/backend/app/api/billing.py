from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services.moneybird import MoneybirdService
from app.services.zitadel import zitadel

Plan = Literal["core", "professional", "complete", "free"]
BillingCycle = Literal["monthly", "yearly"]

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

    user_id: str | None = info.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geen gebruiker gevonden in token",
        )

    result = await db.execute(
        select(PortalOrg)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == user_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisatie niet gevonden",
        )
    return org


class MandateRequest(BaseModel):
    plan: Plan
    billing_cycle: BillingCycle
    seats: int = 1
    # Billing details — required for a legally compliant Dutch invoice
    address: str
    zipcode: str
    city: str
    country: str = "NL"
    # Optional billing fields
    tax_number: str | None = None
    chamber_of_commerce: str | None = None
    billing_email: str | None = None
    internal_reference: str | None = None


@router.post("/mandate")
async def create_mandate(
    request: Request,
    body: MandateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _get_org(credentials, db)

    if settings.mock_billing:
        if not org.moneybird_contact_id:
            org.moneybird_contact_id = "mock"
        org.plan = body.plan
        org.billing_cycle = body.billing_cycle
        org.seats = body.seats
        org.billing_status = "mandate_requested"
        await db.commit()
        base = str(request.base_url).rstrip("/")
        return {"mandate_url": f"{base}/api/billing/mock-complete?org_id={org.id}"}

    try:
        settings.moneybird_product_id(body.plan, body.billing_cycle)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ongeldig token") from exc

    if not org.moneybird_contact_id:
        try:
            contact = await moneybird.create_contact(
                company_name=org.name,
                email=info.get("email"),
                firstname=info.get("given_name"),
                lastname=info.get("family_name"),
                address=body.address,
                zipcode=body.zipcode,
                city=body.city,
                country=body.country,
                tax_number=body.tax_number,
                chamber_of_commerce=body.chamber_of_commerce,
                send_invoices_to_email=body.billing_email,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Kon Moneybird contact niet aanmaken: {exc}",
            ) from exc
        org.moneybird_contact_id = str(contact["id"])

    org.plan = body.plan
    org.billing_cycle = body.billing_cycle
    org.seats = body.seats
    org.billing_status = "mandate_requested"
    await db.commit()

    try:
        mandate_url = await moneybird.get_mandate_url(org.moneybird_contact_id)
    except RuntimeError:
        # Moneybird Payments not yet available (BV not activated) — return null,
        # frontend shows manual-processing notice, billing_status is already set.
        mandate_url = None

    return {"mandate_url": mandate_url}


@router.get("/mock-complete")
async def mock_complete(
    org_id: int,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if not settings.mock_billing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    org.billing_status = "active"
    org.moneybird_subscription_id = "mock"
    await db.commit()
    return RedirectResponse(url=f"{settings.frontend_url}/admin/billing")


@router.get("/status")
async def billing_status(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await _get_org(credentials, db)
    return {
        "billing_status": org.billing_status,
        "plan": org.plan,
        "billing_cycle": org.billing_cycle,
        "seats": org.seats,
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


@router.post("/cancel")
async def cancel_subscription(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org = await _get_org(credentials, db)

    if not org.moneybird_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Geen actief abonnement gevonden",
        )

    try:
        await moneybird.cancel_subscription(org.moneybird_subscription_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon abonnement niet opzeggen: {exc}",
        ) from exc

    org.billing_status = "cancelled"
    await db.commit()

    return {"status": "cancelled"}
