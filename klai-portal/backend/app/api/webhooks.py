import hmac
import logging

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webhook_replay import NonceReplayError, RedisUnavailableError, WebhookNonceStore

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.moneybird import MoneybirdService

logger = logging.getLogger(__name__)
_structlog_logger = structlog.get_logger()

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# @MX:ANCHOR: SPEC-TI-006 / C-9 -- Moneybird replay-protection nonce store.
# @MX:REASON: Single module-scope instance; set_client() is the test hook.
_moneybird_nonce_store = WebhookNonceStore(
    redis_url=settings.redis_url,
    prefix="portal:moneybird-nonce:",
    ttl_seconds=300,
)


async def _check_moneybird_nonce(event_id: str, timestamp: str) -> None:
    """SPEC-TI-006 / C-9: replay check extracted to keep moneybird_webhook complexity in bounds."""
    try:
        await _moneybird_nonce_store.check_and_record(event_id, timestamp)
    except NonceReplayError:
        _structlog_logger.warning("moneybird_webhook_replay_blocked", event_id=event_id)
        raise HTTPException(status_code=409, detail="replay_blocked") from None
    except RedisUnavailableError:
        _structlog_logger.exception("moneybird_webhook_redis_down")
        raise HTTPException(status_code=503, detail="webhook_replay_protection_unavailable") from None


@router.post("/moneybird")
async def moneybird_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    # SPEC-SEC-WEBHOOK-001 REQ-3 + REQ-4:
    # - Startup validator `_require_moneybird_webhook_token` guarantees the
    #   secret is non-empty, so no `if settings.moneybird_webhook_token:` guard
    #   is required or permitted here (REQ-3.2).
    # - Token comparison uses hmac.compare_digest against byte-encoded operands
    #   (REQ-4.1) and auth failure returns HTTP 401, never 200 (REQ-4.2).
    payload: dict = await request.json()
    token = payload.get("webhook_token", "")
    if not hmac.compare_digest(
        token.encode("utf-8"),
        settings.moneybird_webhook_token.encode("utf-8"),
    ):
        _structlog_logger.warning(
            "moneybird_webhook_auth_failed",
            event_type=payload.get("event", ""),
            entity_type=payload.get("entity_type", ""),
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    # SPEC-TI-006 / C-9: replay-protection check.
    # Order: HMAC verify (above) -> replay check -> side-effects.
    event_id = str(payload.get("entity_id") or payload.get("event", ""))
    timestamp = str(payload.get("created_at") or payload.get("entity", {}).get("updated_at", ""))
    await _check_moneybird_nonce(event_id, timestamp)

    entity_type: str = payload.get("entity_type", "")
    event: str = payload.get("event", "")

    if entity_type == "Contact" and event == "contact_mandate_request_succeeded":
        contact_id = str(payload.get("entity", {}).get("id", ""))
        if contact_id:
            result = await db.execute(select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id))
            org = result.scalar_one_or_none()
            if org:
                try:
                    product_id = settings.moneybird_product_id(org.plan, org.billing_cycle)
                except ValueError as exc:
                    logger.exception("Moneybird product ID ontbreekt: %s", exc)
                    return Response(status_code=200)

                frequency_type = "yearly" if org.billing_cycle == "yearly" else "monthly"
                moneybird = MoneybirdService(settings)
                try:
                    subscription = await moneybird.create_subscription(
                        contact_id, product_id, frequency_type, quantity=org.seats
                    )
                    org.moneybird_subscription_id = str(subscription["id"])
                except RuntimeError as exc:
                    logger.exception("Moneybird create_subscription failed: %s", exc)
                finally:
                    await moneybird.close()

                org.billing_status = "active"
                await db.commit()

    elif event == "subscription_cancelled":
        contact_id = str(payload.get("entity", {}).get("contact_id", ""))
        if contact_id:
            result = await db.execute(select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id))
            org = result.scalar_one_or_none()
            if org:
                org.billing_status = "cancelled"
                await db.commit()

    elif event == "invoice_state_changed_to_paid":
        logger.info("Moneybird webhook: invoice_state_changed_to_paid received")

    elif event == "payment_transaction_rejected":
        contact_id = str(payload.get("entity", {}).get("contact_id", ""))
        if contact_id:
            result = await db.execute(select(PortalOrg).where(PortalOrg.moneybird_contact_id == contact_id))
            org = result.scalar_one_or_none()
            if org:
                org.billing_status = "payment_failed"
                await db.commit()

    return Response(status_code=200)
