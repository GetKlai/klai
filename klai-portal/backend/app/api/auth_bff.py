"""
BFF auth endpoints (SPEC-AUTH-008 Phases A2 + A3).

  GET  /api/auth/oidc/start     — begin OIDC authorisation code + PKCE flow
  GET  /api/auth/oidc/callback  — exchange code for tokens, create session
  GET  /api/auth/session        — frontend "who am I" probe
  POST /api/auth/bff/logout     — revoke session + clear cookies (+ Zitadel end_session)

All endpoints are same-origin; the frontend calls them with cookies included.
Only /oidc/callback is driven by a Zitadel redirect — everything else comes
from the SPA itself.
"""

from __future__ import annotations

import time
import urllib.parse

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.session_deps import get_optional_session, get_session
from app.core.config import settings
from app.core.database import get_db
from app.core.session import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionContext,
)
from app.models.portal import PortalUser
from app.services import audit
from app.services.bff_oidc import (
    OidcFlowError,
    build_authorize_url,
    build_end_session_url,
    exchange_code_for_tokens,
    generate_code_verifier,
    generate_state,
    revoke_token,
    s256_challenge,
    verify_id_token,
)
from app.services.bff_session import session_service
from app.services.oidc_pending import oidc_pending

logger = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["auth-bff"])


# ---------------------------------------------------------------------------
# OIDC error classification
# ---------------------------------------------------------------------------
#
# A handful of callback failures are transient and recoverable by simply
# restarting the authorize flow — the user has done nothing wrong and should
# not see an error page. Canonical example: a stale browser-history callback
# URL arrives with a state that was already consumed (or a fresh callback
# races a container restart that dropped the in-memory pieces of the flow).
#
# For those we set a short-lived retry cookie and bounce through `/`, which
# immediately fires signinRedirect(). If a second recoverable failure lands
# while the retry cookie is still present, we stop bouncing and show the
# error page — this prevents infinite loops when something is fundamentally
# broken upstream.
#
# Anything outside the recoverable set is treated as user-visible.

_RECOVERABLE_OIDC_REASONS: frozenset[str] = frozenset({"invalid_state"})
_RETRY_COOKIE_NAME = "klai_oidc_retry"
_RETRY_COOKIE_MAX_AGE_SECONDS = 30


# ---------------------------------------------------------------------------
# GET /api/auth/session
# ---------------------------------------------------------------------------


@router.get("/session")
async def read_session(
    session: SessionContext = Depends(get_session),
) -> dict[str, object]:
    return {
        "authenticated": True,
        "zitadel_user_id": session.zitadel_user_id,
        "csrf_token": session.csrf_token,
        "access_token_expires_at": session.access_token_expires_at,
    }


# ---------------------------------------------------------------------------
# POST /api/auth/bff/logout
#
# Clears the Redis record + both cookies. Always returns 204. When an
# id_token is known, exposes the OIDC end_session URL via
# X-Post-Logout-Redirect so the SPA can follow it and sign the user out
# of Zitadel itself (OIDC Core §5).
# ---------------------------------------------------------------------------


@router.post("/bff/logout", status_code=204)
async def logout(
    session: SessionContext | None = Depends(get_optional_session),
) -> Response:
    id_token_hint: str | None = None
    refresh_token_to_revoke: str | None = None
    audit_resource_id = "unknown"
    audit_actor = "unknown"
    audit_org_id = 0
    if session is not None:
        audit_resource_id = session.sid
        audit_actor = session.zitadel_user_id
        record = await session_service.load(session.sid)
        if record is not None:
            id_token_hint = record.id_token or None
            refresh_token_to_revoke = record.refresh_token or None
            if record.org_id is not None:
                audit_org_id = record.org_id
        await session_service.revoke(session.sid)

    if refresh_token_to_revoke:
        await revoke_token(refresh_token_to_revoke, token_type_hint="refresh_token")  # noqa: S106

    await audit.log_event(
        org_id=audit_org_id,
        actor=audit_actor,
        action="auth.logout",
        resource_type="session",
        resource_id=audit_resource_id,
    )

    response = Response(status_code=204)
    _clear_cookies(response)
    if id_token_hint:
        response.headers["X-Post-Logout-Redirect"] = build_end_session_url(
            id_token_hint=id_token_hint,
            post_logout_redirect_uri=f"{_origin()}/logged-out",
        )
    return response


# ---------------------------------------------------------------------------
# GET /api/auth/oidc/start
# ---------------------------------------------------------------------------


@router.get("/oidc/start")
async def oidc_start(
    request: Request,
    return_to: str = Query("/app"),
    ui_locales: str | None = Query(None),
) -> RedirectResponse:
    safe_return_to = _safe_return_to(return_to)

    state = generate_state()
    code_verifier = generate_code_verifier()
    code_challenge = s256_challenge(code_verifier)
    ua_hash = session_service.hash_metadata(request.headers.get("user-agent"))

    await oidc_pending.put(
        state=state,
        code_verifier=code_verifier,
        return_to=safe_return_to,
        user_agent_hash=ua_hash,
    )

    authorize_url = build_authorize_url(
        state=state,
        code_challenge=code_challenge,
        redirect_uri=_callback_url(),
        ui_locales=ui_locales,
    )
    logger.info("bff_oidc_start", return_to=safe_return_to)
    return RedirectResponse(url=authorize_url, status_code=302)


# ---------------------------------------------------------------------------
# GET /api/auth/oidc/callback
# ---------------------------------------------------------------------------


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if error:
        logger.warning("bff_oidc_callback_op_error", error=error)
        return _fail_redirect(error, request)

    if not code or not state:
        return _fail_redirect("invalid_request", request)

    pending = await oidc_pending.consume(state)
    if pending is None:
        logger.warning("bff_oidc_callback_unknown_state")
        return _fail_redirect("invalid_state", request)

    current_ua_hash = session_service.hash_metadata(request.headers.get("user-agent"))
    if pending.user_agent_hash and pending.user_agent_hash != current_ua_hash:
        logger.info("bff_oidc_callback_ua_mismatch")

    try:
        tokens = await exchange_code_for_tokens(
            code=code,
            code_verifier=pending.code_verifier,
            redirect_uri=_callback_url(),
        )
    except OidcFlowError as exc:
        return _fail_redirect(exc.code or "token_exchange_failed", request)

    try:
        claims = verify_id_token(tokens.id_token)
    except OidcFlowError as exc:
        return _fail_redirect(exc.code or "id_token_invalid", request)

    zitadel_user_id = str(claims.get("sub", ""))
    if not zitadel_user_id:
        return _fail_redirect("id_token_invalid", request)

    portal_row = (
        await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
    ).scalar_one_or_none()
    org_id = portal_row.org_id if portal_row is not None else None

    record = await session_service.create(
        zitadel_user_id=zitadel_user_id,
        org_id=org_id,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_token_expires_at=_access_token_expiry(tokens.expires_in),
        id_token=tokens.id_token,
        user_agent=request.headers.get("user-agent"),
        remote_ip=_client_ip(request),
    )

    # Route through the frontend /callback page so workspaceHandoff() can
    # redirect the user to their tenant subdomain before landing on
    # return_to. Before this change the backend redirected straight to
    # pending.return_to (e.g. /app) and the handoff never ran — every user
    # ended up on my.getklai.com regardless of their workspace_url.
    encoded_return_to = urllib.parse.quote(pending.return_to, safe="/")
    response = RedirectResponse(
        url=f"/callback?return_to={encoded_return_to}",
        status_code=302,
    )
    set_session_cookies(
        response,
        sid=record.sid,
        csrf_token=record.csrf_token,
        max_age_seconds=settings.bff_session_ttl_seconds,
    )
    # A previous recoverable failure may have left the retry cookie set; clear
    # it now that we have a valid session so a future transient failure still
    # gets its one free retry.
    _clear_retry_cookie(response)
    logger.info("bff_oidc_callback_success", zitadel_user_id=zitadel_user_id, org_id=org_id)
    return response


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def set_session_cookies(
    response: Response,
    *,
    sid: str,
    csrf_token: str,
    max_age_seconds: int,
) -> None:
    domain = _cookie_domain()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sid,
        max_age=max_age_seconds,
        path="/",
        domain=domain,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age_seconds,
        path="/",
        domain=domain,
        httponly=False,
        secure=True,
        samesite="lax",
    )


# @MX:ANCHOR: canonical logout-cookie clearing.
# @MX:REASON: Two cookie systems coexist:
#   1. __Secure-klai_session + __Secure-klai_csrf — the SPEC-AUTH-008 BFF pair.
#   2. klai_sso — the SPEC-AUTH-006 Login V2 "remember Zitadel session" cookie,
#      still consumed by /api/auth/sso-complete in auth.py for silent login
#      when the user lands on /login?authRequest=X.
# Logout MUST wipe both. Without klai_sso clearing, any post-logout reauth
# attempt (e.g. a stale tab, or LoginPage auto-signinRedirect firing during
# navigation) re-authenticates the user silently. Product consequence: after
# explicit logout the user enters credentials again — this is desired
# behaviour, not a regression.
def _clear_cookies(response: Response) -> None:
    domain = _cookie_domain()
    for name, httponly in ((SESSION_COOKIE_NAME, True), (CSRF_COOKIE_NAME, False)):
        response.set_cookie(
            name,
            "",
            max_age=0,
            path="/",
            domain=domain,
            httponly=httponly,
            secure=True,
            samesite="lax",
        )
    response.delete_cookie(key="klai_sso", domain=domain or None, path="/")


def _cookie_domain() -> str:
    domain = settings.domain
    if not domain or domain == "localhost":
        return ""
    return f".{domain}" if not domain.startswith(".") else domain


# ---------------------------------------------------------------------------
# OIDC flow helpers
# ---------------------------------------------------------------------------


def _origin() -> str:
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    return f"https://my.{settings.domain}"


def _callback_url() -> str:
    return f"{_origin()}/api/auth/oidc/callback"


def _fail_redirect(reason: str, request: Request) -> RedirectResponse:
    """Redirect away from a failed OIDC callback.

    Recoverable reasons (e.g. `invalid_state`) get ONE silent retry: we set a
    short-lived cookie and redirect to `/`, where LoginPage auto-fires a fresh
    signinRedirect. If the retry cookie is already present we treat the second
    failure as fatal, clear the cookie, and show `/logged-out?reason=<code>`.
    Non-recoverable reasons always show the error page.
    """
    safe_reason = urllib.parse.quote(reason, safe="")
    retry_cookie_present = request.cookies.get(_RETRY_COOKIE_NAME) is not None

    if reason in _RECOVERABLE_OIDC_REASONS and not retry_cookie_present:
        response = RedirectResponse(url=f"{_origin()}/", status_code=302)
        _set_retry_cookie(response)
        logger.info("bff_oidc_callback_auto_retry", reason=reason)
        return response

    response = RedirectResponse(
        url=f"{_origin()}/logged-out?reason={safe_reason}",
        status_code=302,
    )
    _clear_retry_cookie(response)
    return response


def _set_retry_cookie(response: Response) -> None:
    domain = _cookie_domain()
    response.set_cookie(
        _RETRY_COOKIE_NAME,
        "1",
        max_age=_RETRY_COOKIE_MAX_AGE_SECONDS,
        path="/",
        domain=domain,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def _clear_retry_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_RETRY_COOKIE_NAME,
        domain=_cookie_domain() or None,
        path="/",
    )


def _safe_return_to(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/app"
    if "://" in value:
        return "/app"
    return value


def _access_token_expiry(expires_in: int) -> int:
    now = int(time.time())
    return now + (expires_in if expires_in > 0 else 3600)


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None
