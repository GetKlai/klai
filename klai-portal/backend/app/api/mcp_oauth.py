"""MCP OAuth 2.1 authorization-server endpoints (SPEC-MCP-AUTH-001).

Mounts on the root URL space (NOT under ``/api``) because OAuth clients
expect canonical paths:

- ``GET  /.well-known/oauth-authorization-server`` — RFC 8414 metadata
- ``POST /oauth/register``                          — RFC 7591 DCR
- ``GET  /oauth/authorize``                         — start consent flow
- ``POST /oauth/authorize``                         — submit approve/deny
- ``POST /oauth/token``                             — code-exchange + refresh

The connector-OAuth surface (``/api/oauth/google_drive/...``, etc.) lives in
``app/api/oauth.py`` and is unrelated.
"""

from __future__ import annotations

import hmac
import html as _html
import json
import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.session_deps import get_optional_session
from app.core.config import settings
from app.core.database import cross_org_session
from app.core.session import SessionContext
from app.models.portal import PortalOrg, PortalUser
from app.services import audit
from app.services import mcp_oauth as svc
from app.services.redis_client import get_redis_pool

logger = logging.getLogger(__name__)

# No prefix: routes are root-anchored per RFC 8414 / RFC 6749 conventions.
router = APIRouter(tags=["mcp-oauth"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_CONSENT_TEMPLATE_PATH = _TEMPLATES_DIR / "oauth_consent.html"


def _render_consent_page(
    *,
    request_id: str,
    csrf_token: str,
    client_name: str,
    redirect_uri: str,
    application_type: str,
    scopes: list[str],
    user_email: str,
    user_org_name: str,
    is_newly_registered: bool,
) -> HTMLResponse:
    """Render the consent page with simple Python str-replace.

    No Jinja2 dependency — the template is small and the substitution
    surface is bounded. Every interpolated value is HTML-escaped at
    insertion time to prevent XSS via client_name / redirect_uri / etc.
    """
    template = _CONSENT_TEMPLATE_PATH.read_text(encoding="utf-8")

    # Render the {% if is_newly_registered %} block — simple two-state
    # toggle, not a full Jinja replacement.
    if is_newly_registered:
        new_badge = '<span class="badge-new" title="Deze app heeft zich net pas geregistreerd">Net geregistreerd</span>'
        warn_callout = (
            '<div class="warn-callout"><strong>Let op:</strong> '
            "deze app is net geregistreerd. Controleer of de naam en het "
            "callback-adres hieronder kloppen voordat je toestemming geeft."
            "</div>"
        )
    else:
        new_badge = ""
        warn_callout = ""

    # Render the {% if application_type == 'native' %} branch.
    app_type_label = "Desktop / lokale app" if application_type == "native" else "Web-app"

    # Render the {% for scope in scopes %} loop.
    scope_items = []
    for scope in scopes:
        scope_human = (
            "Lezen en bewerken van je persoonlijke en organisatie-kennisbank"
            if scope == "mcp:knowledge"
            else _html.escape(scope)
        )
        scope_items.append(f'<li><span class="scope-icon">✓</span> {scope_human}</li>')
    scopes_block = "\n".join(scope_items)

    # The optional ``( {{ user_org_name | e }})`` segment.
    org_segment = f" ({_html.escape(user_org_name)})" if user_org_name else ""

    # Strip Jinja-style blocks the template still has (the static fallback
    # path) and substitute markers with values. Use unique markers (curly +
    # token) so we don't double-replace.
    rendered = (
        template
        # Drop literal Jinja blocks — they're already simulated above.
        .replace(
            '{% if is_newly_registered %}<span class="badge-new" title="Deze app heeft zich net pas geregistreerd">Net geregistreerd</span>{% endif %}',
            new_badge,
        )
        .replace(
            '{% if is_newly_registered %}\n        <div class="warn-callout">\n            <strong>Let op:</strong> deze app is net geregistreerd. Controleer of de naam en het callback-adres hieronder kloppen voordat je toestemming geeft.\n        </div>\n        {% endif %}',
            warn_callout,
        )
        .replace(
            "{% if application_type == 'native' %}Desktop / lokale app{% else %}Web-app{% endif %}",
            app_type_label,
        )
        # The {% for ... %} block — replace from <ul> open to </ul> close
        # with our pre-rendered scope items wrapped in the same <ul>.
    )
    # For the scopes loop: regex-light approach — locate the open/close.
    start_marker = "{% for scope in scopes %}"
    end_marker = "{% endfor %}"
    start_idx = rendered.find(start_marker)
    end_idx = rendered.find(end_marker, start_idx)
    if start_idx != -1 and end_idx != -1:
        rendered = rendered[:start_idx] + scopes_block + rendered[end_idx + len(end_marker) :]

    # Escape user-controlled values then substitute simple {{ var }} markers.
    safe_vars: dict[str, str] = {
        "{{ request_id | e }}": _html.escape(request_id),
        "{{ csrf_token | e }}": _html.escape(csrf_token),
        "{{ client_name | e }}": _html.escape(client_name),
        "{{ redirect_uri | e }}": _html.escape(redirect_uri),
        "{{ user_email | e }}": _html.escape(user_email),
    }
    for marker, value in safe_vars.items():
        rendered = rendered.replace(marker, value)

    # The org segment: replace the conditional inline expression.
    rendered = rendered.replace(
        "{% if user_org_name %} ({{ user_org_name | e }}){% endif %}",
        org_segment,
    )

    return HTMLResponse(rendered, status_code=200)


# ─── /.well-known/oauth-authorization-server (RFC 8414) ───────────────────


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> JSONResponse:
    """RFC 8414 metadata document advertising this AS's capabilities.

    Per SPEC-MCP-AUTH-001 REQ-7. ``jwks_uri`` is intentionally omitted —
    we issue opaque tokens, not JWTs.
    """
    issuer = settings.mcp_oauth_issuer_base_url.rstrip("/")
    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "scopes_supported": sorted(svc.SUPPORTED_SCOPES),
        "response_types_supported": sorted(svc.SUPPORTED_RESPONSE_TYPES),
        "grant_types_supported": sorted(svc.SUPPORTED_GRANT_TYPES),
        "code_challenge_methods_supported": sorted(svc.SUPPORTED_PKCE_METHODS),
        "token_endpoint_auth_methods_supported": sorted(svc.SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS),
        "resource_servers": [settings.mcp_oauth_resource_url],
    }
    return JSONResponse(metadata, headers={"Cache-Control": "public, max-age=300"})


# ─── POST /oauth/register (RFC 7591 DCR) ──────────────────────────────────


class _DCRRequest(BaseModel):
    """RFC 7591 client metadata. Strict: extra fields rejected."""

    client_name: str = Field(min_length=1, max_length=255)
    redirect_uris: list[str] = Field(min_length=1)
    application_type: str
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None

    model_config = {"extra": "forbid"}


def _client_source_ip(request: Request) -> str:
    """Real client IP via X-Forwarded-For (Caddy front) or peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(request: Request) -> JSONResponse:
    """RFC 7591 Dynamic Client Registration with allowlist + per-IP rate-limit."""
    try:
        body = _DCRRequest.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as exc:
        return _oauth_error("invalid_request", str(exc), 400)

    source_ip = _client_source_ip(request)
    redis = await get_redis_pool()
    if redis is None:
        return _oauth_error("server_error", "Redis unavailable", 503)

    if not await svc.check_dcr_rate_limit(redis, source_ip):
        return _oauth_error("rate_limit_exceeded", "DCR rate limit exceeded", 429)

    async with cross_org_session() as db:
        try:
            registered = await svc.register_client(
                db,
                client_name=body.client_name,
                redirect_uris=body.redirect_uris,
                application_type=body.application_type,
                source_ip=source_ip,
                grant_types=body.grant_types,
                response_types=body.response_types,
            )
        except ValueError as exc:
            message = str(exc)
            if "redirect_uri" in message or "application_type" in message:
                return _oauth_error("invalid_redirect_uri", message, 400)
            return _oauth_error("invalid_request", message, 400)

        await db.commit()

    try:
        await audit.log_event(
            org_id=0,
            actor=f"dcr:{source_ip}",
            action="oauth_client.registered",
            resource_type="oauth_client",
            resource_id=registered.client_id,
            details={
                "client_name": registered.client_name,
                "redirect_uris": registered.redirect_uris,
                "application_type": registered.application_type,
            },
        )
    except Exception:  # pragma: no cover
        logger.warning("oauth_client_registered_audit_failed", exc_info=True)

    return JSONResponse(
        {
            "client_id": registered.client_id,
            "client_name": registered.client_name,
            "redirect_uris": registered.redirect_uris,
            "grant_types": registered.grant_types,
            "response_types": registered.response_types,
            "token_endpoint_auth_method": registered.token_endpoint_auth_method,
            "application_type": registered.application_type,
            "scopes": " ".join(registered.scopes),
        },
        status_code=201,
    )


# ─── /oauth/authorize (consent UI) ─────────────────────────────────────────


async def _resolve_user_org(db: Any, zitadel_user_id: str) -> tuple[PortalUser, PortalOrg] | None:
    """Look up the (PortalUser, PortalOrg) for a session's zitadel_user_id."""
    result = await db.execute(
        select(PortalUser, PortalOrg)
        .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
        .limit(1)
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row else None


@router.get("/oauth/authorize")
async def authorize_get(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str = "",  # RFC 8707 — Claude.ai may omit; default to canonical
    scope: str = svc.DEFAULT_SCOPE,
    state: str = "",
    session: SessionContext | None = Depends(get_optional_session),
) -> Response:
    """Render the consent page (or redirect to login if no session).

    Validates per REQ-13 (PKCE S256), REQ-14 (resource binding), REQ-21
    (login-redirect on missing session).
    """
    if response_type != "code":
        return _oauth_error("unsupported_response_type", response_type, 400)
    if code_challenge_method not in svc.SUPPORTED_PKCE_METHODS:
        return _oauth_error("invalid_request", "PKCE S256 required", 400)
    if not code_challenge or len(code_challenge) < 43:
        return _oauth_error("invalid_request", "code_challenge missing/too short", 400)
    # RFC 8707: if client omits resource we bind to the canonical one.
    # Claude.ai does not always pass `resource=`; rejecting on mismatch
    # would block the consent flow before it starts.
    if not resource:
        resource = settings.mcp_oauth_resource_url
    elif resource != settings.mcp_oauth_resource_url:
        return _oauth_error("invalid_target", "resource mismatch", 400)

    requested_scopes = scope.split()
    if not set(requested_scopes).issubset(svc.SUPPORTED_SCOPES):
        return _oauth_error("invalid_scope", scope, 400)

    # Look up client + validate redirect_uri (cross_org_session because the
    # clients table is org-overstijgend and we don't have tenant context yet).
    async with cross_org_session() as db:
        client = await svc.get_client_by_id(db, client_id)
        if client is None:
            return _oauth_error("invalid_client", "unknown client_id", 400)
        if redirect_uri not in client.redirect_uris:
            return _oauth_error("invalid_redirect_uri", "redirect_uri not registered", 400)
        client_name = client.client_name
        application_type = client.application_type
        client_created_at = client.created_at

    # REQ-21: redirect to login if no session.
    if session is None:
        return_to = f"{request.url.path}?{request.url.query}"
        login_url = f"/login?return_to={urlencode({'r': return_to})[2:]}"
        return RedirectResponse(login_url, status_code=302)

    # Resolve user-email + org-name for the consent page UI.
    async with cross_org_session() as db:
        user_org = await _resolve_user_org(db, session.zitadel_user_id)
        if user_org is None:
            return _oauth_error("login_required", "session has no portal user", 401)
        user, org = user_org
        user_email = user.email or session.zitadel_user_id
        org_name = org.name

    # Persist the pending auth-request in Redis; the request_id is the
    # opaque key the consent-form submit POSTs back.
    redis = await get_redis_pool()
    if redis is None:
        return _oauth_error("server_error", "Redis unavailable", 503)

    request_id = await svc.create_auth_request(
        redis,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scopes=requested_scopes,
        state=state,
        resource=resource,
    )

    # CSRF token: opaque, stored alongside the auth-request and verified on POST.
    csrf_token = secrets.token_urlsafe(24)
    await redis.set(
        f"oauth:csrf:{request_id}",
        csrf_token,
        ex=600,  # same TTL as the auth-request itself
    )

    # "Newly registered" badge: client < 5 min old → mitigation for confused
    # deputy phishing per Risks table.
    now_utc = datetime.now(UTC)
    is_newly_registered = (now_utc - client_created_at.replace(tzinfo=UTC)).total_seconds() < 300

    return _render_consent_page(
        request_id=request_id,
        csrf_token=csrf_token,
        client_name=client_name,
        redirect_uri=redirect_uri,
        application_type=application_type,
        scopes=requested_scopes,
        user_email=user_email,
        user_org_name=org_name,
        is_newly_registered=is_newly_registered,
    )


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    request_id: str = Form(...),
    csrf_token: str = Form(...),
    decision: str = Form(...),
    session: SessionContext | None = Depends(get_optional_session),
) -> Response:
    """Handle approve/deny submit from the consent page."""
    if session is None:
        return _oauth_error("login_required", "session expired", 401)

    redis = await get_redis_pool()
    if redis is None:
        return _oauth_error("server_error", "Redis unavailable", 503)

    # CSRF validation — must match the token issued on GET.
    stored_csrf_raw = await redis.get(f"oauth:csrf:{request_id}")
    stored_csrf = stored_csrf_raw.decode() if isinstance(stored_csrf_raw, bytes) else stored_csrf_raw
    if not stored_csrf:
        return _oauth_error("invalid_request", "expired or unknown request", 400)
    if not hmac.compare_digest(stored_csrf, csrf_token):
        return _oauth_error("invalid_request", "CSRF token mismatch", 400)
    # Single-use CSRF: drop after first match.
    await redis.delete(f"oauth:csrf:{request_id}")

    pending = await svc.fetch_auth_request(redis, request_id)
    if pending is None:
        return _oauth_error("invalid_request", "expired or unknown request", 400)

    if decision != "approve":
        # Deny path — redirect with error.
        params: dict[str, str] = {"error": "access_denied"}
        if pending.state:
            params["state"] = pending.state
        return RedirectResponse(f"{pending.redirect_uri}?{urlencode(params)}", status_code=302)

    # Resolve portal_users.id + portal_orgs.id for the session's user.
    async with cross_org_session() as db:
        user_org = await _resolve_user_org(db, session.zitadel_user_id)
        if user_org is None:
            return _oauth_error("login_required", "session has no portal user", 401)
        user, org = user_org

    code = await svc.approve_auth_request(
        redis,
        request_id,
        user_id=user.id,
        org_id=org.id,
    )
    if code is None:
        return _oauth_error("invalid_request", "expired", 400)

    params = {"code": code}
    if pending.state:
        params["state"] = pending.state
    return RedirectResponse(f"{pending.redirect_uri}?{urlencode(params)}", status_code=302)


# ─── POST /oauth/token (authorization_code + refresh_token) ───────────────


@router.post("/oauth/token")
async def token_endpoint(request: Request) -> JSONResponse:
    """RFC 6749 token endpoint. Two grant types:

    - ``authorization_code`` — code-exchange + PKCE verify
    - ``refresh_token``      — rotation + replay-detection
    """
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))

    if grant_type == "authorization_code":
        return await _exchange_authorization_code(form)
    if grant_type == "refresh_token":
        return await _exchange_refresh_token(form)
    return _oauth_error("unsupported_grant_type", grant_type, 400)


async def _exchange_authorization_code(form: Any) -> JSONResponse:
    code = str(form.get("code", ""))
    code_verifier = str(form.get("code_verifier", ""))
    redirect_uri = str(form.get("redirect_uri", ""))
    client_id_param = str(form.get("client_id", ""))

    if not code or not code_verifier or not client_id_param:
        return _oauth_error("invalid_request", "missing required params", 400)

    redis = await get_redis_pool()
    if redis is None:
        return _oauth_error("server_error", "Redis unavailable", 503)

    payload = await svc.consume_auth_code(redis, code)
    if payload is None:
        return _oauth_error("invalid_grant", "code not found or already used", 400)

    if payload["client_id"] != client_id_param:
        return _oauth_error("invalid_grant", "client_id mismatch", 400)
    if payload["redirect_uri"] != redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri mismatch", 400)
    if not svc.verify_pkce_s256(code_verifier, payload["code_challenge"]):
        return _oauth_error("invalid_grant", "PKCE verification failed", 400)

    async with cross_org_session() as db:
        client = await svc.get_client_by_id(db, payload["client_id"])
        if client is None:
            return _oauth_error("invalid_grant", "unknown client", 400)

        issued = await svc.issue_token_pair(
            db,
            org_id=payload["org_id"],
            user_id=payload["user_id"],
            client_db_id=client.id,
            scopes=payload.get("scopes", [svc.DEFAULT_SCOPE]),
            resource_uri=payload["resource"],
        )
        await db.commit()

    try:
        await audit.log_event(
            org_id=payload["org_id"],
            actor=str(payload["user_id"]),
            action="mcp_token.issued",
            resource_type="mcp_token",
            resource_id=str(issued.token_id),
            details={
                "client_id": client.client_id,
                "client_name": client.client_name,
                "scopes": payload.get("scopes", [svc.DEFAULT_SCOPE]),
                "expires_in": issued.expires_in,
            },
        )
    except Exception:  # pragma: no cover
        logger.warning("mcp_token_issued_audit_failed", exc_info=True)

    return JSONResponse(
        {
            "access_token": issued.access_token,
            "token_type": "Bearer",
            "expires_in": issued.expires_in,
            "refresh_token": issued.refresh_token,
            "refresh_expires_in": issued.refresh_expires_in,
            "scope": " ".join(payload.get("scopes", [svc.DEFAULT_SCOPE])),
        }
    )


async def _exchange_refresh_token(form: Any) -> JSONResponse:
    refresh_token = str(form.get("refresh_token", ""))
    if not refresh_token:
        return _oauth_error("invalid_request", "missing refresh_token", 400)

    redis = await get_redis_pool()
    if redis is None:
        return _oauth_error("server_error", "Redis unavailable", 503)

    async with cross_org_session() as db:
        outcome = await svc.refresh_access_token(
            db,
            redis,
            raw_refresh_token=refresh_token,
            expected_resource=settings.mcp_oauth_resource_url,
        )
        await db.commit()

    if outcome.success is None:
        return _oauth_error("invalid_grant", outcome.failure_reason or "invalid", 400)

    issued = outcome.success
    try:
        await audit.log_event(
            org_id=0,
            actor=f"mcp_token:{issued.token_id}",
            action="mcp_token.refreshed",
            resource_type="mcp_token",
            resource_id=str(issued.token_id),
            details={"expires_in": issued.expires_in},
        )
    except Exception:  # pragma: no cover
        logger.warning("mcp_token_refreshed_audit_failed", exc_info=True)

    return JSONResponse(
        {
            "access_token": issued.access_token,
            "token_type": "Bearer",
            "expires_in": issued.expires_in,
            "refresh_token": issued.refresh_token,
            "refresh_expires_in": issued.refresh_expires_in,
            "scope": svc.DEFAULT_SCOPE,
        }
    )


# ─── Error helper ─────────────────────────────────────────────────────────


def _oauth_error(error: str, description: str, status_code: int) -> JSONResponse:
    """RFC 6749 § 5.2 error response shape."""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )
