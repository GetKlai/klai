"""
klai-mailer — Zitadel HTTP notification provider.

Endpoints:
  GET  /health   Liveness check for Docker
  POST /notify   Zitadel webhook (requires Authorization: Bearer <secret>)
  POST /debug    Log raw payload to verify field names (DEBUG=true only)
"""

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.mailer import send_email
from app.models import ZitadelPayload
from app.portal_client import get_user_language
from app.renderer import Renderer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="klai-mailer", docs_url=None, redoc_url=None, openapi_url=None)

renderer = Renderer(theme_dir=Path(settings.theme_dir))

_branding = {
    "logo_url": settings.logo_url,
    "logo_width": settings.logo_width,
    "brand_url": settings.brand_url,
}


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _verify_zitadel_signature(raw_body: bytes, signature_header: str | None) -> None:
    """
    Verify the ZITADEL-Signature header.

    Format: t={timestamp},v1={hmac_hex}
    Signed payload: {timestamp}.{raw_body}
    Algorithm: HMAC-SHA256 with the signing key.
    """
    if not signature_header:
        logger.warning("Webhook call received without ZITADEL-Signature header")
        raise HTTPException(status_code=401, detail="Missing ZITADEL-Signature header")

    parts = {k: v for k, v in (p.split("=", 1) for p in signature_header.split(",") if "=" in p)}
    timestamp = parts.get("t")
    v1 = parts.get("v1")

    if not timestamp or not v1:
        logger.warning("Malformed ZITADEL-Signature header: %s", signature_header)
        raise HTTPException(status_code=401, detail="Malformed ZITADEL-Signature header")

    # Reject replayed webhooks older than 5 minutes
    try:
        ts_age = abs(int(timestamp) - int(time.time()))
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed ZITADEL-Signature header")
    if ts_age > 300:
        logger.warning("ZITADEL-Signature timestamp too old: %s", timestamp)
        raise HTTPException(status_code=401, detail="Webhook timestamp too old")

    signed_payload = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(settings.webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, v1):
        logger.warning("ZITADEL-Signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/notify")
async def notify(request: Request) -> JSONResponse:
    """
    Receive a Zitadel notification, render Klai-branded HTML, send via SMTP.

    Returns 200 on success (Zitadel marks notification as sent).
    Returns 500 on render/SMTP failure (Zitadel will retry).
    """
    raw_body = await request.body()
    _verify_zitadel_signature(raw_body, request.headers.get("zitadel-signature"))

    if settings.debug:
        logger.info("RAW PAYLOAD: %s", raw_body.decode(errors="replace"))

    payload = ZitadelPayload.model_validate_json(raw_body)
    to_address = payload.recipient_email()
    logger.info("Received notification type=%s to=%s", payload.event_type(), to_address)

    if not to_address:
        logger.error("No recipient email in payload for event_type=%s", payload.event_type())
        raise HTTPException(status_code=422, detail="No recipient email address in payload")

    lang = await get_user_language(to_address)
    render_result = renderer.render(payload, lang=lang)
    html_email = renderer.wrap(render_result, _branding)

    await send_email(
        to_address=to_address,
        subject=render_result["subject"],
        html_body=html_email,
    )

    return JSONResponse(status_code=200, content={"sent": True})


@app.post("/debug")
async def debug(request: Request) -> JSONResponse:
    """
    Log and echo the raw Zitadel payload.
    Use this immediately after deploying to verify field names match the models.
    Only available when DEBUG=true.
    """
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")

    raw_body = await request.body()
    _verify_zitadel_signature(raw_body, request.headers.get("zitadel-signature"))

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        parsed = {"raw": raw_body.decode(errors="replace")}

    logger.info("DEBUG payload:\n%s", json.dumps(parsed, indent=2, ensure_ascii=False))
    return JSONResponse(status_code=200, content={"received": parsed})
