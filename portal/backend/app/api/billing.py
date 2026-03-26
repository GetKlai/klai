import logging
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
from app.services.events import emit_event
from app.services.moneybird import MoneybirdService
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

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
) -> tuple[PortalOrg, str]:
    """Return (org, zitadel_user_id) for the authenticated user."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    user_id: str | None = info.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No user found in token",
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
            detail="Organisation not found",
        )
    return org, user_id


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
    org, user_id = await _get_org(credentials, db)

    if settings.mock_billing:
        if not org.moneybird_contact_id:
            org.moneybird_contact_id = "mock"
        old_plan = org.plan
        org.plan = body.plan
        org.billing_cycle = body.billing_cycle
        org.seats = body.seats
        org.billing_status = "mandate_requested"
        await db.commit()
        emit_event(
            "billing.plan_changed",
            org_id=org.id,
            user_id=user_id,
            properties={"from_plan": old_plan, "to_plan": body.plan, "billing_cycle": body.billing_cycle},
        )
        base = str(request.base_url).rstrip("/")
        return {"mandate_url": f"{base}/api/billing/mock-complete?org_id={org.id}"}

    try:
        settings.moneybird_product_id(body.plan, body.billing_cycle)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

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
            logger.error("Moneybird contact creation failed for org %d: %s", org.id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to create Moneybird contact: {exc}",
            ) from exc
        org.moneybird_contact_id = str(contact["id"])

    old_plan = org.plan
    org.plan = body.plan
    org.billing_cycle = body.billing_cycle
    org.seats = body.seats
    org.billing_status = "mandate_requested"
    await db.commit()
    emit_event(
        "billing.plan_changed",
        org_id=org.id,
        user_id=user_id,
        properties={"from_plan": old_plan, "to_plan": body.plan, "billing_cycle": body.billing_cycle},
    )

    logger.info("Billing mandate requested for org %d, plan=%s", org.id, body.plan)

    try:
        mandate_url = await moneybird.get_mandate_url(org.moneybird_contact_id)
    except RuntimeError as exc:
        logger.warning("Mandate URL fetch failed for org %d, falling back: %s", org.id, exc)
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
    org, _ = await _get_org(credentials, db)
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
    org, _ = await _get_org(credentials, db)

    if not org.moneybird_contact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Moneybird contact linked",
        )

    try:
        portal_url = await moneybird.get_invoice_portal_url(org.moneybird_contact_id)
    except RuntimeError as exc:
        logger.warning("Invoice portal URL fetch failed for org %d: %s", org.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve invoice portal URL: {exc}",
        ) from exc

    return {"portal_url": portal_url}


@router.post("/cancel")
async def cancel_subscription(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    moneybird: MoneybirdService = Depends(get_moneybird),
) -> dict:
    org, user_id = await _get_org(credentials, db)

    if not org.moneybird_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription found",
        )

    try:
        await moneybird.cancel_subscription(org.moneybird_subscription_id)
    except RuntimeError as exc:
        logger.error("Subscription cancellation failed for org %d: %s", org.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to cancel subscription: {exc}",
        ) from exc

    org.billing_status = "cancelled"
    await db.commit()
    emit_event("billing.cancelled", org_id=org.id, user_id=user_id, properties={"plan": org.plan})

    return {"status": "cancelled"}
