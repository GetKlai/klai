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
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jinja2 import TemplateNotFound, UndefinedError
from jinja2.exceptions import SecurityError
from pydantic import ValidationError

from app.config import settings
from app.logging_setup import RequestContextMiddleware, setup_logging
from app.mailer import send_email
from app.models import ZitadelPayload
from app.nonce import (
    NonceReplayError,
    RedisUnavailableError,
    check_and_record_nonce,
)
from app.portal_client import (
    PortalLookupError,
    get_user_language,
    resolve_org_admin_email,
)
from app.rate_limit import check_and_record as check_rate_limit
from app.renderer import Renderer
from app.schemas import TEMPLATE_SCHEMAS
from app.signature import SignatureError, verify_zitadel_signature

setup_logging()

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()

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


async def _verify_zitadel_signature(
    raw_body: bytes, signature_header: str | None
) -> dict[str, str]:
    """Wrapper around app.signature + app.nonce with uniform 401 / 503.

    REQ-7.1: every signature failure returns HTTP 401 body
             {"detail": "invalid signature"}.
    REQ-7.2: the failure `reason` is logged, not leaked to the response.
    REQ-7.3: no side-channel response headers.
    REQ-10:  unknown vN fields, >5 tokens, and unknown non-vN keys all rejected
             via the SignatureError("unknown_vN_field") path.
    REQ-6.1: nonce check runs AFTER HMAC verification to prevent cache pollution
             by forged signatures.
    REQ-6.3: Redis unreachable → HTTP 503 (fail-closed). The nonce check is
             a security control; no silent bypass on degraded infra.
    """
    try:
        parts = verify_zitadel_signature(raw_body, signature_header, settings.webhook_secret)
    except SignatureError as exc:
        struct_logger.warning("mailer_signature_invalid", reason=exc.reason, **exc.extra)
        raise HTTPException(status_code=401, detail="invalid signature") from exc

    try:
        await check_and_record_nonce(parts)
    except NonceReplayError as exc:
        struct_logger.warning("mailer_signature_invalid", reason="replay")
        raise HTTPException(status_code=401, detail="invalid signature") from exc
    except RedisUnavailableError as exc:
        struct_logger.error("mailer_nonce_redis_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail="Service unavailable") from exc

    return parts


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
    await _verify_zitadel_signature(raw_body, request.headers.get("zitadel-signature"))

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
#
# Hardening (SPEC-SEC-MAILER-INJECTION-001):
# - REQ-1: SandboxedEnvironment + StrictUndefined via Renderer.render_internal
# - REQ-2: per-template Pydantic schema (TEMPLATE_SCHEMAS) with extra=forbid
# - REQ-3: recipient bound to template-derived expectation
# - REQ-4: per-recipient rate limit
# - REQ-8: hmac.compare_digest on X-Internal-Secret (via _validate_incoming_secret)
# ---------------------------------------------------------------------------


_SUPPORTED_LOCALES = frozenset({"nl", "en"})


def _truncate_error_value(value: Any, limit: int = 80) -> Any:
    """REQ-2.3: truncate long str values in pydantic errors before logging.

    Prevents log-bomb / reflection of an attacker-supplied large string.
    """
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value


async def _resolve_expected_recipient(
    template_name: str,
    validated_vars: Any,
    supplied_to: str,
) -> str:
    """Return the authoritative recipient address for this template.

    REQ-3.1: `join_request_admin` → portal-api callback.
    REQ-3.2: `join_request_approved` → validated_vars.email (supplied `to`
             must match case-insensitively).

    Raises HTTPException on any mismatch / lookup failure. Callers MUST
    NOT bypass this — the recipient-binding invariant is the cap on the
    SMTP-relay attack surface (finding mailer-3).
    """
    supplied_norm = (supplied_to or "").strip().lower()

    if template_name == "join_request_admin":
        try:
            expected = await resolve_org_admin_email(validated_vars.org_id)
        except PortalLookupError as exc:
            raise HTTPException(
                status_code=503,
                detail="recipient lookup unavailable",
            ) from exc
        if supplied_norm != expected.strip().lower():
            struct_logger.warning(
                "mailer_recipient_mismatch",
                template=template_name,
                expected_hash=hashlib.sha256(expected.strip().lower().encode()).hexdigest(),
                supplied_hash=hashlib.sha256(supplied_norm.encode()).hexdigest(),
            )
            raise HTTPException(status_code=400, detail="recipient mismatch")
        return expected

    if template_name == "join_request_approved":
        expected = str(validated_vars.email).strip().lower()
        # REQ-3.2: accept `to` match OR use variables.email directly. We
        # require match when `to` is supplied, else use the schema field.
        if supplied_norm and supplied_norm != expected:
            struct_logger.warning(
                "mailer_recipient_mismatch",
                template=template_name,
                expected_hash=hashlib.sha256(expected.encode()).hexdigest(),
                supplied_hash=hashlib.sha256(supplied_norm.encode()).hexdigest(),
            )
            raise HTTPException(status_code=400, detail="recipient mismatch")
        return str(validated_vars.email)

    # Fallback for any template that somehow bypassed TEMPLATE_SCHEMAS —
    # defensively 400 rather than passing through attacker `to`.
    raise HTTPException(status_code=400, detail=f"Unknown template: {template_name}")


@app.post("/internal/send")
async def internal_send(request: Request) -> JSONResponse:
    """Send a transactional email using a predefined template.

    Authenticated via X-Internal-Secret header. Every request passes through:
      1. Constant-time secret check (REQ-8)
      2. Per-template Pydantic schema validation (REQ-2)
      3. Recipient binding to template-derived expectation (REQ-3)
      4. Per-recipient Redis rate limit (REQ-4)
      5. Jinja2 SandboxedEnvironment render (REQ-1)
    """
    _validate_incoming_secret(request.headers.get("X-Internal-Secret"))

    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid JSON body")

    template_name = body.get("template", "")
    to_address = body.get("to", "")
    locale = body.get("locale", "nl")
    variables = body.get("variables") or {}

    if locale not in _SUPPORTED_LOCALES:
        locale = "nl"

    # REQ-2.2: resolve per-template schema
    schema_cls = TEMPLATE_SCHEMAS.get(template_name)
    if schema_cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown template: {template_name}")

    # REQ-2.3: schema validation, attacker-supplied values truncated in errors
    try:
        validated = schema_cls.model_validate(variables)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            errors.append({
                "loc": err.get("loc"),
                "msg": err.get("msg"),
                "type": err.get("type"),
                "input": _truncate_error_value(err.get("input")),
            })
        struct_logger.warning(
            "mailer_template_schema_invalid",
            template=template_name,
            errors=errors,
        )
        raise HTTPException(
            status_code=400,
            detail="invalid variables",
        ) from exc

    # REQ-3: bind recipient. Any mismatch / lookup failure short-circuits
    # BEFORE the rate-limit increment (REQ-4.5).
    expected_recipient = await _resolve_expected_recipient(
        template_name, validated, to_address
    )

    # REQ-4: per-recipient rate limit (AFTER validation, BEFORE dispatch).
    decision = await check_rate_limit(expected_recipient)
    if not decision.allowed:
        struct_logger.warning(
            "mailer_recipient_rate_limited",
            template=template_name,
            recipient_hash=decision.recipient_hash,
        )
        return JSONResponse(
            status_code=429,
            content={"detail": "recipient rate limit exceeded"},
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    # REQ-1 + REQ-2.4: construct render context. Branding comes from
    # settings, NEVER from the request body.
    render_context: dict[str, Any] = validated.model_dump()
    # Normalise URL / email types that dump to non-str by default
    for k, v in list(render_context.items()):
        if not isinstance(v, str | int | float | bool):
            render_context[k] = str(v)
    render_context["brand_url"] = settings.brand_url

    try:
        rendered = renderer.render_internal(template_name, locale, render_context)
    except (TemplateNotFound, UndefinedError) as exc:
        struct_logger.warning(
            "mailer_template_schema_invalid",
            template=template_name,
            reason="missing_variable",
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail="missing required variable") from exc
    except SecurityError as exc:
        struct_logger.warning(
            "mailer_template_sandbox_violation",
            template=template_name,
            reason="sandbox_error",
        )
        raise HTTPException(status_code=400, detail="unexpected placeholder") from exc

    # Internal templates supply their own body HTML; fill the Zitadel-shaped
    # wrapper context with empty defaults so StrictUndefined does not trip.
    wrapper_context = {
        "subject": rendered["subject"],
        "preheader": "",
        "body_html": rendered["body"],
        "has_button": False,
        "button_text": "",
        "button_url": "",
        "footer_note": "",
    }
    html_email = renderer.wrap(wrapper_context, _branding)

    await send_email(
        to_address=expected_recipient,
        subject=rendered["subject"],
        html_body=html_email,
    )
    struct_logger.info(
        "mailer_internal_email_sent",
        template=template_name,
        recipient_hash=decision.recipient_hash,
    )

    return JSONResponse(status_code=200, content={"sent": True})


def _debug_enabled() -> bool:
    """Both gates must pass for /debug to be reachable.

    REQ-5.3: drives conditional route registration below.
    REQ-5.4: also checked inside the handler body (defence in depth).
    """
    return settings.debug and settings.portal_env != "production"


async def _debug_handler(request: Request) -> JSONResponse:
    """
    Log and echo the raw Zitadel payload.
    Use this immediately after deploying to verify field names match the models.
    Only available when DEBUG=true AND PORTAL_ENV != production.
    """
    # REQ-5.4: handler-level fallback gate. MUST hold even if REQ-5.3's
    # conditional registration is forgotten in a future refactor.
    if not _debug_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    raw_body = await request.body()
    await _verify_zitadel_signature(raw_body, request.headers.get("zitadel-signature"))

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        parsed = {"raw": raw_body.decode(errors="replace")}

    logger.info("DEBUG payload:\n%s", json.dumps(parsed, indent=2, ensure_ascii=False))
    return JSONResponse(status_code=200, content={"received": parsed})


# REQ-5.4 (MUST) is the authoritative defence: the handler itself re-checks
# the gate on every call. We register the route unconditionally so the
# response body is always our canonical `{"detail": "Not found"}` rather
# than Starlette's default `Not Found` — consistent content-type, consistent
# casing, no leaking of "this endpoint is registered but gated" via the
# body shape. REQ-5.3 (conditional registration) is a SHOULD; we trade it
# for a consistent 404 body across environments.
app.post("/debug")(_debug_handler)
