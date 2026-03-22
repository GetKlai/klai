import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.moneybird import MoneybirdService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/moneybird")
async def moneybird_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    payload: dict = await request.json()

    if settings.moneybird_webhook_token:
        token = payload.get("webhook_token", "")
        if token != settings.moneybird_webhook_token:
            logger.warning("Moneybird webhook: invalid token")
            return Response(status_code=200)

    entity_type: str = payload.get("entity_type", "")
    event: str = payload.get("event", "")

    if entity_type == "Contact" and event == "contact_mandate_request_succeeded":
        contact_id = str(payload.get("entity", {}).get("id", ""))
        if contact_id:
            result = await db.execute(
                select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id)
            )
            org = result.scalar_one_or_none()
            if org:
                try:
                    product_id = settings.moneybird_product_id(org.plan, org.billing_cycle)
                except ValueError as exc:
                    logger.error("Moneybird product ID ontbreekt: %s", exc)
                    return Response(status_code=200)

                frequency_type = "yearly" if org.billing_cycle == "yearly" else "monthly"
                moneybird = MoneybirdService(settings)
                try:
                    subscription = await moneybird.create_subscription(
                        contact_id, product_id, frequency_type, quantity=org.seats
                    )
                    org.moneybird_subscription_id = str(subscription["id"])
                except RuntimeError as exc:
                    logger.error("Moneybird create_subscription failed: %s", exc)
                finally:
                    await moneybird.close()

                org.billing_status = "active"
                await db.commit()

    elif event == "subscription_cancelled":
        contact_id = str(payload.get("entity", {}).get("contact_id", ""))
        if contact_id:
            result = await db.execute(
                select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id)
            )
            org = result.scalar_one_or_none()
            if org:
                org.billing_status = "cancelled"
                await db.commit()

    elif event == "invoice_state_changed_to_paid":
        logger.info("Moneybird webhook: invoice_state_changed_to_paid received")

    elif event == "payment_transaction_rejected":
        contact_id = str(payload.get("entity", {}).get("contact_id", ""))
        if contact_id:
            result = await db.execute(
                select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id)
            )
            org = result.scalar_one_or_none()
            if org:
                org.billing_status = "payment_failed"
                await db.commit()

    return Response(status_code=200)
