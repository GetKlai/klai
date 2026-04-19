"""
BFF auth endpoints — session read + logout (SPEC-AUTH-008 Phase A2).

Later phases will add:
  - GET  /api/auth/oidc/start     — begin OIDC authorisation code + PKCE flow
  - GET  /api/auth/oidc/callback  — exchange code for tokens, create session
  - POST /api/auth/refresh        — explicit refresh (middleware also refreshes
                                     automatically near expiry)

This module is intentionally narrow: the endpoints here are the ones the
frontend relies on once a session exists. Until Phase A3 ships, sessions
can only be created out-of-band (e.g. via tests), so /session / /logout
work against whatever the SessionMiddleware resolved.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response

from app.api.session_deps import get_optional_session, get_session
from app.core.config import settings
from app.core.session import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SessionContext,
)
from app.services.bff_session import session_service

logger = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["auth-bff"])


# ---------------------------------------------------------------------------
# GET /api/auth/session
#
# Cheap "who am I" probe the frontend calls on mount. Returns 401 when the
# session cookie is missing, stale, or the Redis record has expired. Returns
# the zitadel_user_id + CSRF token so the client can mirror it in the
# X-CSRF-Token header for mutations.
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
# Clears the Redis record + both cookies. Always returns 204 — including when
# no session was present, so a double-logout from two tabs never 401s.
#
# The path is namespaced under /bff/ during the migration soak so it does not
# collide with the existing SPEC-AUTH-006 /api/auth/logout (which clears the
# klai_sso cookie). Phase E will merge the two endpoints into a single
# canonical /api/auth/logout that handles both flows.
#
# Phase A3 will additionally call Zitadel /oauth/v2/end_session with the stored
# id_token_hint so the user is also signed out of the OP.
# ---------------------------------------------------------------------------


@router.post("/bff/logout", status_code=204)
async def logout(
    session: SessionContext | None = Depends(get_optional_session),
) -> Response:
    if session is not None:
        await session_service.revoke(session.sid)

    response = Response(status_code=204)
    _clear_cookies(response)
    return response


# ---------------------------------------------------------------------------
# Cookie helpers
#
# Single place that knows the cookie attribute set. set_session_cookies is
# exported for the OIDC callback handler that lands in Phase A3.
# ---------------------------------------------------------------------------


def set_session_cookies(
    response: Response,
    *,
    sid: str,
    csrf_token: str,
    max_age_seconds: int,
) -> None:
    """Attach the paired __Secure-klai_session (HttpOnly) + __Secure-klai_csrf cookies.

    Domain is set to `.<domain>` so the session is shared across the main
    portal and every tenant subdomain.
    """
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


def _cookie_domain() -> str:
    """Domain= attribute for session cookies.

    In production we want `.getklai.com` so tenant subdomains share the session.
    In local dev the domain is `localhost` (or whatever settings.domain is);
    browsers refuse `Domain=localhost` cookies, so we leave it unset.
    """
    domain = settings.domain
    if not domain or domain == "localhost":
        return ""
    return f".{domain}" if not domain.startswith(".") else domain
