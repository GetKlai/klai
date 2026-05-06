"""MCP OAuth 2.1 authorization-server endpoints (SPEC-MCP-AUTH-001).

Mounts on the root URL space (NOT under ``/api``) because OAuth clients
expect canonical paths:

- ``GET  /.well-known/oauth-authorization-server`` — RFC 8414 metadata
- ``POST /oauth/register``                          — RFC 7591 DCR
- ``GET  /oauth/authorize``                         — start consent flow
- ``POST /oauth/authorize``                         — submit approve/deny
- ``POST /oauth/token``                             — code-exchange + refresh

Scope of this file in v0.2.1 (current commit):

- WELL-KNOWN metadata: ✅ implemented
- DCR: ✅ implemented (anonymous registration with allowlist + per-IP rate-limit)
- TOKEN exchange (refresh-grant): ✅ implemented
- AUTHORIZE consent flow: ⚠️ STUB — returns 501 with a clear message that
  this endpoint requires BFF session integration that is staged for the
  next implementation cycle. The wire shape and validation are already
  correct; what is missing is the consent-page rendering and the user-
  approve POST handling that needs to bind to the existing
  ``app.api.session_deps.get_optional_session`` BFF session.
- TOKEN exchange (authorization_code grant): ⚠️ STUB — same reason.

The connector-OAuth surface (``/api/oauth/google_drive/...``, etc.) lives in
``app/api/oauth.py`` and is unrelated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.database import cross_org_session
from app.services import audit
from app.services import mcp_oauth as svc
from app.services.redis_client import get_redis_pool

logger = logging.getLogger(__name__)

# No prefix: routes are root-anchored per RFC 8414 / RFC 6749 conventions.
router = APIRouter(tags=["mcp-oauth"])


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
    """Real client IP via X-Forwarded-For (Caddy front) or peer.

    Per research.md §11: Caddy is the only legitimate front for traffic
    reaching this endpoint, so X-Forwarded-For (when present) is the real
    client IP.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(request: Request) -> JSONResponse:
    """RFC 7591 Dynamic Client Registration.

    Anonymous endpoint — no auth required. Defenses: per-IP rate-limit
    (REQ-27), allowlist on redirect_uris (REQ-20), strict application_type
    matching (REQ-13a).
    """
    try:
        body = _DCRRequest.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as exc:
        return _oauth_error("invalid_request", str(exc), 400)

    source_ip = _client_source_ip(request)
    redis = await get_redis_pool()
    if redis is None:
        # Fail-closed: without Redis we can't rate-limit, and MCP OAuth
        # explicitly requires Redis as a hard dep (auth_request_store).
        return _oauth_error("server_error", "Redis unavailable", 503)

    if not await svc.check_dcr_rate_limit(redis, source_ip):
        return _oauth_error(
            "rate_limit_exceeded",
            "DCR rate limit exceeded for source IP",
            429,
        )

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

    # Audit emit (independent session inside log_event)
    try:
        await audit.log_event(
            org_id=0,  # DCR is org-overstijgend
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
    except Exception as exc:  # pragma: no cover - audit must never break the flow
        logger.warning("oauth_client_registered_audit_failed", exc_info=True)
        del exc

    response_body = {
        "client_id": registered.client_id,
        "client_name": registered.client_name,
        "redirect_uris": registered.redirect_uris,
        "grant_types": registered.grant_types,
        "response_types": registered.response_types,
        "token_endpoint_auth_method": registered.token_endpoint_auth_method,
        "application_type": registered.application_type,
        "scopes": " ".join(registered.scopes),
    }
    return JSONResponse(response_body, status_code=201)


# ─── GET / POST /oauth/authorize — STUB (next implementation cycle) ───────


@router.get("/oauth/authorize")
@router.post("/oauth/authorize")
async def authorize_stub() -> JSONResponse:
    """STUB: consent-flow endpoint awaiting BFF session integration.

    In the v0.2.1 commit this endpoint returns HTTP 501 with a structured
    ``not_implemented`` error so OAuth clients fail fast and operators can
    grep the access-log for it.

    Implementation TODO for the next cycle:
    1. Use ``Depends(get_optional_session)`` to resolve the BFF session.
    2. If no session → ``RedirectResponse`` to ``/login?return_to=...`` with
       the URL-encoded original authorize URL preserved.
    3. Render ``app/templates/oauth_consent.html`` with client metadata,
       scopes, and the request_id stored via ``svc.create_auth_request``.
    4. Handle approve/deny POST submit; on approve mint an auth-code via
       ``svc.approve_auth_request`` and 302 to ``redirect_uri?code=&state=``.

    The full implementation outline is in ``app/api/mcp_oauth.py`` git history
    on commit ``e8c4f1a3`` of feature/mcp-auth-001 (this same branch, prior
    snapshot before the v0.2.1 scope-reduction).

    See SPEC-MCP-AUTH-001 § Implementation plan Fase 2b.
    """
    return _oauth_error(
        "not_implemented",
        "consent-flow endpoint pending BFF session integration; see SPEC-MCP-AUTH-001 Fase 2b",
        501,
    )


# ─── POST /oauth/token ────────────────────────────────────────────────────


@router.post("/oauth/token")
async def token_endpoint(request: Request) -> JSONResponse:
    """RFC 6749 token endpoint.

    Two grant types supported per SPEC-MCP-AUTH-001:

    - ``refresh_token``      — ✅ implemented (rotation + replay-detection)
    - ``authorization_code`` — ⚠️ stubbed pending /oauth/authorize (501)
    """
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))

    if grant_type == "refresh_token":
        return await _exchange_refresh_token(form)
    if grant_type == "authorization_code":
        return _oauth_error(
            "not_implemented",
            "authorization_code grant pending /oauth/authorize implementation; see SPEC-MCP-AUTH-001 Fase 2b",
            501,
        )
    return _oauth_error("unsupported_grant_type", grant_type, 400)


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

    # Audit (best-effort)
    try:
        await audit.log_event(
            org_id=0,  # refresh-flow doesn't carry org_id at this layer
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
