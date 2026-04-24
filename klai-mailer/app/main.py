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
from app.logging_setup import RequestContextMiddleware, setup_logging
from app.mailer import send_email
from app.models import ZitadelPayload
from app.portal_client import get_user_language
from app.renderer import Renderer

setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(title="klai-mailer", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(RequestContextMiddleware)

renderer = Renderer(theme_dir=Path(settings.theme_dir))

_branding = {
    "logo_url": settings.logo_url,
    "logo_width": settings.logo_width,
    "brand_url": settings.brand_url,
}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _validate_incoming_secret(header_value: str | None) -> None:
    """Constant-time check of X-Internal-Secret against settings.internal_secret.

    REQ-8: uses hmac.compare_digest to prevent a timing oracle on the shared
    secret. This helper is the single authoritative comparison for any
    /internal/* endpoint — never reintroduce direct-equality comparison
    against the settings value.
    """
    supplied = (header_value or "").encode("utf-8")
    expected = settings.internal_secret.encode("utf-8")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


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


# ---------------------------------------------------------------------------
# Internal send endpoint (portal-api → mailer, SPEC-AUTH-006 R7)
# ---------------------------------------------------------------------------

# Simple templates for internal transactional emails
_INTERNAL_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "join_request_admin": {
        "nl": {
            "subject": "[Klai] Toegangsverzoek van {name} ({email})",
            "body": (
                "<p>Hallo,</p>"
                "<p><strong>{name}</strong> ({email}) heeft een toegangsverzoek ingediend voor je Klai-werkruimte.</p>"
                "<p>Je kunt het verzoek goedkeuren of afwijzen in de <a href='{brand_url}/admin/settings/join-requests'>beheeromgeving</a>.</p>"
            ),
        },
        "en": {
            "subject": "[Klai] Access request from {name} ({email})",
            "body": (
                "<p>Hello,</p>"
                "<p><strong>{name}</strong> ({email}) has submitted an access request for your Klai workspace.</p>"
                "<p>You can approve or deny the request in the <a href='{brand_url}/admin/settings/join-requests'>admin settings</a>.</p>"
            ),
        },
    },
    "join_request_approved": {
        "nl": {
            "subject": "[Klai] Je toegangsverzoek is goedgekeurd",
            "body": (
                "<p>Hallo {name},</p>"
                "<p>Je toegangsverzoek voor Klai is goedgekeurd. Je kunt nu inloggen op je werkruimte:</p>"
                "<p><a href='{workspace_url}'>{workspace_url}</a></p>"
            ),
        },
        "en": {
            "subject": "[Klai] Your access request has been approved",
            "body": (
                "<p>Hello {name},</p>"
                "<p>Your access request for Klai has been approved. You can now log in to your workspace:</p>"
                "<p><a href='{workspace_url}'>{workspace_url}</a></p>"
            ),
        },
    },
}


@app.post("/internal/send")
async def internal_send(request: Request) -> JSONResponse:
    """Send a transactional email using a predefined template.

    Authenticated via X-Internal-Secret header (same as portal-api internal endpoints).
    """
    _validate_incoming_secret(request.headers.get("X-Internal-Secret"))

    body = json.loads(await request.body())
    template_name = body.get("template", "")
    to_address = body.get("to", "")
    locale = body.get("locale", "nl")
    variables = body.get("variables", {})

    template = _INTERNAL_TEMPLATES.get(template_name)
    if not template:
        raise HTTPException(status_code=400, detail=f"Unknown template: {template_name}")

    lang_template = template.get(locale, template.get("nl", {}))

    # Add branding vars
    variables["brand_url"] = settings.brand_url

    subject = lang_template["subject"].format(**variables)
    body_html = lang_template["body"].format(**variables)

    # Wrap in the Klai email template
    html_email = renderer.wrap(
        {"subject": subject, "body": body_html, "button_url": "", "button_text": ""},
        _branding,
    )

    if to_address:
        await send_email(to_address=to_address, subject=subject, html_body=html_email)
        logger.info("Internal email sent template=%s to=%s", template_name, to_address)
    else:
        logger.warning("No to_address for internal email template=%s", template_name)

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
