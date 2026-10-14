"""Widget bootstrap routes: /widget-config (GET and CORS preflight) and /public-bot-config.

These mint the widget session token and hand the widget its theme, starters and integrations.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api.partner_hubspot import _hubspot_handoff_enabled_for_widget
from app.api.widget_public import WIDGET_ROUTE_DECIDES_CORS
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.core.permissions import assert_platform_unlocked
from app.models.portal import PortalOrg
from app.models.widgets import Widget, WidgetKbAccess
from app.services.partner_rate_limit import check_rate_limit
from app.services.redis_client import get_redis_pool
from app.services.request_ip import resolve_caller_ip
from app.services.widget_audit import (
    hash_audit_value,
)
from app.services.widget_auth import generate_session_token, origin_allowed
from app.services.widget_theme import _merge_css_variables

logger = structlog.get_logger()

router = APIRouter()
_WIDGET_CLIENT_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


# ---------------------------------------------------------------------------
# Widget CORS header builder
#
# SPEC-SEC-CORS-001 REQ-2.2 — partner widget endpoints SHALL NEVER set
# Access-Control-Allow-Credentials: true. Widget traffic authenticates via
# Bearer JWT in the Authorization header; cookies are not involved. The
# helper centralises this contract so the GET and OPTIONS handlers can
# never drift apart.
#
# /widget-config carries the WIDGET_ROUTE_DECIDES_CORS marker (see
# app/api/widget_public.py): the per-widget allowed_origins gate below needs
# a DB read, so its routes answer CORS themselves and KlaiCORSMiddleware
# passes those requests through untouched — unlike the "echo" marker the
# other widget routes carry.
# ---------------------------------------------------------------------------


def _widget_cors_headers(origin: str, *, preflight: bool) -> dict[str, str]:
    """Build the CORS response headers for /partner/v1/widget-config.

    The actual server-side origin gate is the per-widget `allowed_origins`
    check upstream of this call (see ``origin_allowed`` in
    ``app.services.widget_auth``). This helper only constructs the response
    headers once that gate has approved the request.

    Parameters
    ----------
    origin:
        The request Origin header value, already validated against the
        widget's allowed_origins list. The caller MUST NOT pass an
        unvalidated origin — that is the upstream check's responsibility.
    preflight:
        When True, includes the preflight-only headers (Allow-Methods,
        Allow-Headers, Max-Age). When False, returns the minimal set for
        an actual response.
    """
    headers: dict[str, str] = {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }
    if preflight:
        headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Klai-Widget-Session-Id"
        headers["Access-Control-Max-Age"] = "86400"
    return headers


def _widget_client_session_id(request: Request | None) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        header_value = headers.get("x-klai-widget-session-id") or headers.get("X-Klai-Widget-Session-Id")
        if isinstance(header_value, str) and header_value:
            return header_value if _WIDGET_CLIENT_SESSION_RE.fullmatch(header_value) else None
    query_params = getattr(request, "query_params", None)
    if query_params is None or not hasattr(query_params, "get"):
        return None
    value = query_params.get("session_id")
    if not isinstance(value, str) or not value:
        return None
    return value if _WIDGET_CLIENT_SESSION_RE.fullmatch(value) else None


def _validated_http_url(url: object) -> str:
    """Return the value unchanged when it is an absolute http(s) URL, else ``""``.

    Shared scheme gate for every admin-entered URL that reaches a visitor's
    browser: ``javascript:``, other schemes, relative and malformed values
    are dropped here, before the widget ever sees them.
    """
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _widget_booking_url(widget_config_data: dict[str, Any]) -> str:
    """INTERIM — remove together with ``booking_url`` when the chat booking
    API integration replaces the support-partner redirect.

    Return the widget's booking URL for the visitor-facing appointment
    button, or ``""`` to hide it. The stored value is admin input that
    lands in an ``href`` in a visitor's browser, so only absolute http(s)
    URLs are delivered at all (see ``_validated_http_url``). An unset
    field changes nothing about the payload's behaviour.
    """
    return _validated_http_url(widget_config_data.get("booking_url"))


def _widget_conversation_starters(widget_config_data: dict[str, Any]) -> list[str]:
    """Starter chips for the empty-state, capped at 3.

    The admin form and its backing model reject more than 3 on save, but a
    widget saved before that cap dropped from 6 can still carry up to 6
    stored entries. Truncate here so every widget shows at most 3 chips
    immediately, without requiring the tenant to re-save.
    """
    starters = widget_config_data.get("conversation_starters")
    return starters[:3] if isinstance(starters, list) else []


def _widget_nerds_integration(widget_config_data: dict[str, Any]) -> dict[str, Any]:
    """Nerds booking panel (Voys-specific support-partner integration).

    Delivered to the visitor-facing widget-config endpoints as
    ``{"enabled": bool, "booking_url": str}``. The booking URL is admin
    input that becomes an iframe ``src`` in a visitor's browser, so it runs
    through the same absolute-http(s) gate as the interim booking redirect:
    a ``javascript:`` or relative value never reaches the visitor. A panel
    without a loadable URL is no panel and a switched-off integration has
    no visitor to serve, so ``enabled`` only survives when the toggle is on
    AND the URL validates — and an empty ``booking_url`` is delivered
    whenever the link is off. A widget without this integration gets the
    same disabled shape as before it existed.
    """
    integrations = widget_config_data.get("integrations")
    nerds = integrations.get("nerds") if isinstance(integrations, dict) else None
    if not isinstance(nerds, dict) or not nerds.get("enabled"):
        return {"enabled": False, "booking_url": ""}
    booking_url = _validated_http_url(nerds.get("booking_url"))
    return {"enabled": bool(booking_url), "booking_url": booking_url}


# ---------------------------------------------------------------------------
# GET /partner/v1/widget-config  (SPEC-WIDGET-001 Task 2)
# Public endpoint — NO auth dependency
# ---------------------------------------------------------------------------

# REQ-7 (Finding B-4) abuse ceilings for the public mint paths. The per-widget
# number used to be 10/min, which was a traffic cap, not an abuse cap: every
# visitor of every page embedding the widget shares one bucket (help.voys.nl
# hit it on the 11th visitor/min). Now: 20/min per client IP (real visitors get
# an individual budget) under a 120/min per-widget backstop (order of magnitude
# above real traffic, mirrors the Caddy partner_per_ip edge zone). Actual LLM
# drain stays bounded by the untouched per-widget 60/min chat-auth bucket in
# partner_dependencies._auth_via_session_token.
_WIDGET_MINT_IP_RATE_LIMIT_PER_MINUTE = 20
_WIDGET_MINT_RATE_LIMIT_PER_MINUTE = 120


def _widget_mint_rate_limited(retry_after: int, origin: str) -> Response:
    """429 shared by the public mint paths (REQ-7 / Finding B-4).

    The origin is echoed (no credentials) so a browser on the customer's
    site sees the 429 and its Retry-After instead of an opaque CORS error;
    the response carries nothing the widget allowlist protects."""
    headers = {"Retry-After": str(retry_after)}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Expose-Headers"] = "Retry-After"
        headers["Vary"] = "Origin"
    return Response(
        content='{"detail":"Rate limit exceeded"}',
        status_code=429,
        media_type="application/json",
        headers=headers,
    )


@router.get("/widget-config", openapi_extra=WIDGET_ROUTE_DECIDES_CORS)
async def widget_config(
    id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return widget bootstrap configuration and a short-lived session token.

    # @MX:WARN: [AUTO] Public endpoint — no authentication required
    # @MX:REASON: Origin check is UX-only (see docstring §"Security model");
    # the actual security boundary is the HS256 JWT session_token. Token TTL 1h;
    # no sensitive data returned. SPEC-SEC-HYGIENE-001 REQ-23.
    # @MX:SPEC: SPEC-WIDGET-001 REQ-2 + SPEC-SEC-HYGIENE-001 REQ-23

    SPEC-WIDGET-002: Public endpoint, no API key required.
    - Looks up widget by widget_id (id param) in the widgets table
    - Validates Origin header against allowed_origins (UX-gating only — see below)
    - Generates HS256 JWT session token (1 hour TTL)
    - Returns CORS headers for matched origin (never *)

    Security model (SPEC-SEC-HYGIENE-001 REQ-23.1):

    The ``Origin`` header check is **UX-only, not a security boundary.**
    Auditors flag this finding repeatedly because non-browser clients
    (curl, custom integrations) can spoof the ``Origin`` header — yes,
    they can, and that is fine, because:

    - The primary identifier is ``widget_id`` (the URL ``id`` query
      parameter). It is a public, opaque identifier.
    - Downstream security (chat completions, KB retrieval) is enforced
      by the HS256 JWT ``session_token`` returned in the response body.
      The token carries ``wgt_id``, ``org_id``, and the allowed
      ``kb_ids`` — it is the actual access-control mechanism.
    - A non-browser client that spoofs ``Origin`` receives the same
      scoped session_token any other browser would receive for that
      widget. They cannot escalate privilege; they can only obtain a
      token that grants access to exactly the KBs the widget owner has
      already published.
    - ``allowed_origins`` therefore controls **browser embedding
      behaviour** (which origins may render the widget iframe), not
      API access control.

    Asymmetric signing (ES256/EdDSA) is the structural fix and is
    tracked separately; until that lands, REQ-24 derives per-tenant
    HS256 keys via HKDF so a single secret leak does not let an attacker
    forge tokens cross-tenant.

    Error codes:
        404 - widget_id not found
        403 - missing or disallowed Origin (UX gate)
        503 - WIDGET_JWT_SECRET not configured
    """
    # Check JWT secret is configured
    if not settings.widget_jwt_secret:
        logger.warning("widget_jwt_secret_not_configured")
        return Response(
            content='{"detail":"Widget authentication not configured"}',
            status_code=503,
            media_type="application/json",
        )

    # REQ-7 (Finding B-4): mint rate-limit BEFORE DB lookup, two layers, checked
    # client-first so a rejected attempt never counts against the shared widget
    # ceiling — one hammering client can only ever use its own IP share of it:
    # 1. per client IP — the caller IP comes from uvicorn's proxy-header
    #    validation behind Caddy (app.services.request_ip), so it is not
    #    spoofable like the widget session-id header.
    # 2. per widget — the REQ-7 abuse ceiling and backstop: key-rotating
    #    clients still stop here.
    # @MX:NOTE: [AUTO] Rate-limit keys are widget_mint_ip:{id}:{caller_ip} and
    # widget_mint:{id} (public widget_id from URL param).
    # Limits: 20/min per client IP, 120/min per-widget ceiling — see
    # _WIDGET_MINT_RATE_LIMIT_PER_MINUTE for why the old 10/min was a traffic cap.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
    redis = await get_redis_pool()
    if redis is not None:
        caller_ip = resolve_caller_ip(request)
        client_allowed, client_retry_after = await check_rate_limit(
            redis,
            f"widget_mint_ip:{id}:{caller_ip}",
            limit_per_minute=_WIDGET_MINT_IP_RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
        )
        if not client_allowed:
            # Visitor IPs are logged hashed, never raw (widget-audit convention).
            logger.warning(
                "widget_mint_rate_limited",
                path="widget-config",
                widget_id=id,
                limit="client",
                ip_hash=hash_audit_value(caller_ip),
            )
            return _widget_mint_rate_limited(client_retry_after, request.headers.get("origin", ""))

        allowed, retry_after = await check_rate_limit(
            redis, f"widget_mint:{id}", limit_per_minute=_WIDGET_MINT_RATE_LIMIT_PER_MINUTE, window_seconds=60
        )
        if not allowed:
            # Review finding 5: the ceiling event carries the same ip_hash as the
            # client event — a rejected visitor is traceable whichever layer fired.
            logger.warning(
                "widget_mint_rate_limited",
                path="widget-config",
                widget_id=id,
                limit="widget",
                ip_hash=hash_audit_value(caller_ip),
            )
            return _widget_mint_rate_limited(retry_after, request.headers.get("origin", ""))

    # Look up widget by public widget_id (SPEC-WIDGET-002: own table)
    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
    widget_row = result.scalar_one_or_none()

    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # Validate Origin header
    origin = request.headers.get("origin", "")
    widget_config_data = widget_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins, allow_any_origin=widget_row.allow_any_origin):
        return Response(
            content='{"detail":"Origin not allowed"}',
            status_code=403,
            media_type="application/json",
        )

    # Load org and set tenant BEFORE KB access query (ensures RLS context is active)
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == widget_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # REQ-1 (Finding B-1): existence-non-disclosure — surface as 404 not 403.
    # @MX:ANCHOR: [AUTO] Platform-unlock gate on widget_config public endpoint
    # @MX:REASON: Fencing 'widgets' in enabled_addons must also block the public mint path;
    # admin-UI gate alone leaves deployed widgets draining LLM tokens for locked tenants.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1
    try:
        assert_platform_unlocked(org, "widgets")
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found") from None

    await set_tenant(db, org.id)

    # Load KB access for this widget (after RLS tenant is set)
    kb_result = await db.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget_row.id))
    kb_rows = kb_result.scalars().all()
    kb_ids = [row.kb_id for row in kb_rows]

    # Generate session token. SPEC-SEC-HYGIENE-001 REQ-24.4: pass tenant_slug
    # so the signing key is HKDF-derived per tenant — a single-secret leak no
    # longer lets an attacker forge tokens across tenants.
    session_token = generate_session_token(
        wgt_id=widget_row.widget_id,
        org_id=widget_row.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
        session_id=_widget_client_session_id(request),
    )

    expires_at = datetime.now(UTC) + timedelta(hours=1)

    body = {
        "title": widget_config_data.get("title", "") or widget_row.name,
        "welcome_message": widget_config_data.get("welcome_message", ""),
        "css_variables": _merge_css_variables(widget_config_data, getattr(org, "widget_css_variables", None)),
        "chat_endpoint": "/partner/v1/chat/completions",
        "session_token": session_token,
        "session_expires_at": expires_at.isoformat(),
        # TWD-style additions: chips and the optional AI-intro toggle.
        # system_prompt stays server-side only; footer Markdown is a separate
        # presentation field interpreted by the widget.
        "conversation_starters": _widget_conversation_starters(widget_config_data),
        "hide_disclaimer": widget_config_data.get("hide_disclaimer", False),
        "footer_text": widget_config_data.get("footer_text"),
        # Opt-in per widget: footer links then open in the widget's own in-chat
        # panel instead of a new tab. Absent → False, i.e. the new-tab default.
        "footer_links_in_widget": widget_config_data.get("footer_links_in_widget", False),
        # Name drives the TWD-pattern header (avatar + title). description
        # is admin-only scope/behaviour config, not rendered to visitors —
        # kept in the response only as a widget-frontend locale hint.
        "name": widget_row.name,
        "description": widget_row.description or "",
        # Explicit tenant text preserves empty versus null: empty hides the
        # introduction, while null keeps the widget's legacy default.
        "ai_disclosure_override": widget_config_data.get("ai_disclosure_override"),
        "primary_color": widget_config_data.get("primary_color", "#fcaa2d"),
        "theme": widget_config_data.get("theme", "light"),
        "collect_user_info": widget_config_data.get("collect_user_info", False),
        "widget_position": widget_config_data.get("widget_position", "right"),
        # Display toggles: widget renders the sources block / meta line
        # under each assistant message based on these flags.
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
        "page_context_enabled": widget_config_data.get("page_context_enabled", False),
        # Interim appointment redirect (booking API pending) — see _widget_booking_url.
        "booking_url": _widget_booking_url(widget_config_data),
        # Voys-specific: in-widget Nerds booking panel — see _widget_nerds_integration.
        "nerds": _widget_nerds_integration(widget_config_data),
        "handoff": {
            "hubspot": {"enabled": _hubspot_handoff_enabled_for_widget(org=org, widget_config_data=widget_config_data)}
        },
    }

    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers=_widget_cors_headers(origin, preflight=False),
    )


@router.get("/public-bot-config")
async def public_bot_config(
    id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Public bot share-link endpoint — no Origin check.

    Powers the share-link bot page at /bot/{widget_id}. Anyone with the
    widget_id (a public, opaque identifier) can fetch the same payload
    as /widget-config without going through the browser-embed origin
    gate. Downstream security is the HS256 JWT session_token, identical
    to the embed flow.
    """
    if not settings.widget_jwt_secret:
        return Response(
            content='{"detail":"Widget authentication not configured"}',
            status_code=503,
            media_type="application/json",
        )

    # REQ-7 (Finding B-4): mint rate-limit BEFORE DB lookup — same two layers,
    # keys and order as widget_config. Without the per-client layer here, one
    # share-link client could burn the whole shared widget_mint:{id} ceiling and
    # lock out the embed visitors of the same widget (Caddy's edge zone is also
    # 120/min/IP, so it does not stop that).
    # @MX:NOTE: [AUTO] Rate-limit keys are widget_mint_ip:{id}:{caller_ip} and
    # widget_mint:{id}, deliberately shared with the embed path: one client has
    # one 20/min mint budget per widget no matter which of the two public doors
    # it enters, under the same 120/min per-widget abuse ceiling.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
    redis = await get_redis_pool()
    if redis is not None:
        caller_ip = resolve_caller_ip(request)
        client_allowed, client_retry_after = await check_rate_limit(
            redis,
            f"widget_mint_ip:{id}:{caller_ip}",
            limit_per_minute=_WIDGET_MINT_IP_RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
        )
        if not client_allowed:
            # Visitor IPs are logged hashed, never raw (widget-audit convention).
            logger.warning(
                "widget_mint_rate_limited",
                path="public-bot-config",
                widget_id=id,
                limit="client",
                ip_hash=hash_audit_value(caller_ip),
            )
            return _widget_mint_rate_limited(client_retry_after, request.headers.get("origin", ""))

        allowed, retry_after = await check_rate_limit(
            redis, f"widget_mint:{id}", limit_per_minute=_WIDGET_MINT_RATE_LIMIT_PER_MINUTE, window_seconds=60
        )
        if not allowed:
            logger.warning(
                "widget_mint_rate_limited",
                path="public-bot-config",
                widget_id=id,
                limit="widget",
                ip_hash=hash_audit_value(caller_ip),
            )
            return _widget_mint_rate_limited(retry_after, request.headers.get("origin", ""))

    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
    widget_row = result.scalar_one_or_none()
    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    widget_config_data = widget_row.widget_config or {}
    if not widget_row.public_share_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == widget_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    # REQ-1 (Finding B-1): existence-non-disclosure — surface as 404 not 403.
    # @MX:ANCHOR: [AUTO] Platform-unlock gate on public_bot_config endpoint
    # @MX:REASON: Same as widget_config — disabled tenant still has live share links;
    # 404 avoids leaking widget existence for locked tenants.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1
    try:
        assert_platform_unlocked(org, "widgets")
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found") from None

    await set_tenant(db, org.id)

    kb_result = await db.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget_row.id))
    kb_ids = [row.kb_id for row in kb_result.scalars().all()]

    session_token = generate_session_token(
        wgt_id=widget_row.widget_id,
        org_id=widget_row.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    body = {
        "title": widget_config_data.get("title", ""),
        "welcome_message": widget_config_data.get("welcome_message", ""),
        "css_variables": _merge_css_variables(widget_config_data, getattr(org, "widget_css_variables", None)),
        "chat_endpoint": "/partner/v1/chat/completions",
        "session_token": session_token,
        "session_expires_at": expires_at.isoformat(),
        "conversation_starters": _widget_conversation_starters(widget_config_data),
        "hide_disclaimer": widget_config_data.get("hide_disclaimer", False),
        "footer_text": widget_config_data.get("footer_text"),
        "footer_links_in_widget": widget_config_data.get("footer_links_in_widget", False),
        "primary_color": widget_config_data.get("primary_color", "#fcaa2d"),
        "theme": widget_config_data.get("theme", "light"),
        "collect_user_info": widget_config_data.get("collect_user_info", False),
        "widget_position": widget_config_data.get("widget_position", "right"),
        "show_sources": widget_config_data.get("show_sources", True),
        "show_meta": widget_config_data.get("show_meta", False),
        "page_context_enabled": widget_config_data.get("page_context_enabled", False),
        # Interim appointment redirect (booking API pending) — see _widget_booking_url.
        "booking_url": _widget_booking_url(widget_config_data),
        # Voys-specific: in-widget Nerds booking panel — see _widget_nerds_integration.
        "nerds": _widget_nerds_integration(widget_config_data),
        "name": widget_row.name,
        "description": widget_row.description or "",
        "ai_disclosure_override": widget_config_data.get("ai_disclosure_override"),
        "handoff": {
            "hubspot": {
                # org is None only if the row vanished between the two queries;
                # treat that as not-enabled rather than raising here.
                "enabled": org is not None
                and _hubspot_handoff_enabled_for_widget(org=org, widget_config_data=widget_config_data)
            }
        },
    }
    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
    )


@router.options("/widget-config", openapi_extra=WIDGET_ROUTE_DECIDES_CORS)
async def widget_config_preflight(
    id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Handle OPTIONS preflight for widget-config CORS.

    SPEC-WIDGET-001: Return 204 with CORS headers for valid origins.
    Returns CORS headers without verifying JWT secret (preflight only).

    @MX:WARN: DB read precedes the origin allowlist check. A cross-origin
    attacker probing this endpoint with rotating origins causes one DB hit
    per attempt. Browsers cache rejected preflights only briefly so the
    cost can compound under sustained probing.
    @MX:REASON: Origin validation must read ``widget.allowed_origins`` from
    the DB; we cannot decide without that read. A future SPEC may add a
    ``widget_id -> allowed_origins`` cache (60s TTL in Redis) — see PR #180
    follow-up. Preserved as pre-existing pattern under SPEC-SEC-CORS-001
    minimal-changes scope.
    @MX:SPEC: SPEC-WIDGET-001
    """
    # REQ-16: soft-deleted widgets are 404 to the public/partner endpoints.
    result = await db.execute(select(Widget).where(Widget.widget_id == id, Widget.deleted_at.is_(None)))
    widget_row = result.scalar_one_or_none()

    if widget_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

    origin = request.headers.get("origin", "")
    widget_config_data = widget_row.widget_config or {}
    allowed_origins = widget_config_data.get("allowed_origins", [])

    if not origin or not origin_allowed(origin, allowed_origins, allow_any_origin=widget_row.allow_any_origin):
        return Response(status_code=204)

    return Response(
        status_code=204,
        headers=_widget_cors_headers(origin, preflight=True),
    )
