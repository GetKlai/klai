import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.moneybird import MoneybirdService
from app.services.widget_handoff import record_hubspot_agent_reply

logger = logging.getLogger(__name__)
_structlog_logger = structlog.get_logger()

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_HUBSPOT_SIGNATURE_MAX_AGE_SECONDS = 5 * 60
_HUBSPOT_URI_DECODE_MAP = {
    "%3A": ":",
    "%2F": "/",
    "%3F": "?",
    "%40": "@",
    "%21": "!",
    "%24": "$",
    "%27": "'",
    "%28": "(",
    "%29": ")",
    "%2A": "*",
    "%2C": ",",
    "%3B": ";",
}


def _hubspot_request_uri(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    uri = f"{proto}://{host}{path}"
    for encoded, decoded in _HUBSPOT_URI_DECODE_MAP.items():
        uri = uri.replace(encoded, decoded)
    return uri  # nosemgrep: python.flask.security.audit.directly-returned-format-string.directly-returned-format-string


def _verify_hubspot_signature_v3(
    *,
    method: str,
    request_uri: str,
    request_body: bytes,
    timestamp: str | None,
    signature: str | None,
    client_secret: str,
    now_ms: int | None = None,
) -> bool:
    if not timestamp or not signature or not client_secret:
        return False

    try:
        timestamp_ms = int(timestamp)
    except ValueError:
        return False

    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if abs(now_ms - timestamp_ms) > _HUBSPOT_SIGNATURE_MAX_AGE_SECONDS * 1000:
        return False

    source = method.upper().encode("utf-8") + request_uri.encode("utf-8") + request_body + timestamp.encode("utf-8")
    expected = base64.b64encode(hmac.new(client_secret.encode("utf-8"), source, hashlib.sha256).digest()).decode(
        "ascii"
    )
    return hmac.compare_digest(expected.encode("utf-8"), signature.encode("utf-8"))


def _hubspot_event_log_fields(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, dict):
        message = {}

    text = message.get("text")
    rich_text = message.get("richText")
    channel_integration_thread_ids = message.get("channelIntegrationThreadIds")
    if isinstance(channel_integration_thread_ids, list):
        integration_thread_ids = [str(value) for value in channel_integration_thread_ids]
    else:
        integration_thread_ids = []

    return {
        "event_type": payload.get("type"),
        "portal_id": payload.get("portalId"),
        "channel_id": payload.get("channelId"),
        "channel_account_id": message.get("channelAccountId") or payload.get("channelAccountId"),
        "conversations_thread_id": message.get("conversationsThreadId") or message.get("threadId"),
        "message_id": message.get("id"),
        "message_status": message.get("status"),
        "integration_thread_ids": integration_thread_ids,
        "text_length": len(text) if isinstance(text, str) else None,
        "rich_text_length": len(rich_text) if isinstance(rich_text, str) else None,
    }


@router.post("/hubspot/custom-channel")
async def hubspot_custom_channel_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    request_body = await request.body()
    signature = request.headers.get("x-hubspot-signature-v3")
    timestamp = request.headers.get("x-hubspot-request-timestamp")

    if not _verify_hubspot_signature_v3(
        method=request.method,
        request_uri=_hubspot_request_uri(request),
        request_body=request_body,
        timestamp=timestamp,
        signature=signature,
        client_secret=settings.hubspot_webchat_client_secret,
    ):
        _structlog_logger.warning("hubspot_custom_channel_webhook_auth_failed")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    _structlog_logger.info(
        "hubspot_custom_channel_webhook_received",
        **_hubspot_event_log_fields(payload),
    )
    result = await record_hubspot_agent_reply(db, payload)
    _structlog_logger.info(
        "hubspot_custom_channel_webhook_processed",
        status=result.get("status"),
        reason=result.get("reason"),
        handoff_session_id=result.get("handoff_session_id"),
    )
    return Response(status_code=204)


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
