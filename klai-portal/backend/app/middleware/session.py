"""
BFF SessionMiddleware — resolves the session cookie on every request (SPEC-AUTH-008).

Runs before every route so `request.state.session` is always set to either a
populated :class:`~app.core.session.SessionContext` (authenticated) or `None`
(unauthenticated). Also performs the CSRF double-submit check on state-changing
methods when a session is present.

Routes read `request.state.session` via the `get_session` dependency. This
middleware MUST run before LoggingContextMiddleware so that `org_id` / `user_id`
land in every log entry.
"""

from __future__ import annotations

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.session import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionContext,
)
from app.services.bff_session import SessionRecord, session_service

logger = structlog.get_logger()

# Methods considered "safe" — never mutate server state and are exempt from CSRF.
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Route prefixes that intentionally operate without a session.
#
# Format contract (SPEC-SEC-CORS-001 REQ-4.1, enforced by
# tests/test_csrf_exempt_rationale.py):
#   1. Each entry is preceded (within 5 lines above) by a comment block.
#   2. The block contains at least one rationale keyword from the set:
#      pre-session, no session, sendBeacon, internal, partner, widget,
#      Zitadel, signup, health probe.
#   3. The block ends with a standalone trailing line of the form
#      `# REQ-X.Y / AC-Z` (or `AC-A, AC-B` when multiple ACs apply). The
#      lint test `test_csrf_exempt_rationale_format_is_canonical` enforces
#      this exact shape so future contributors cannot drift the format.
#
# The CORS allowlist (REQ-1) is the browser-side gate that makes these
# exemptions safe: cross-origin credentialed probing is blocked before any
# request reaches these endpoints (AC-2, AC-7).
_CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    # pre-session: OIDC flow INITIATES the session — no BFF cookie exists yet.
    # REQ-1.2 / AC-2
    "/api/auth/oidc/start",
    # pre-session: OIDC callback COMPLETES the session — no BFF cookie yet.
    # REQ-1.2 / AC-2
    "/api/auth/oidc/callback",
    # pre-session: Zitadel IDP intent start (Google/Microsoft SSO redirect).
    # No BFF session exists; Zitadel redirects back after external IDP auth.
    # REQ-1.2 / AC-2
    "/api/auth/idp-intent",
    # pre-session: Zitadel IDP intent callback — finalises external IDP login.
    # Same pre-session condition as /api/auth/idp-intent.
    # REQ-1.2 / AC-2
    "/api/auth/idp-callback",
    # Zitadel Login V2 password finisher — called from my.getklai.com/login
    # without a portal BFF session. A stale cookie from a previous BFF login
    # would otherwise cause the CSRF check to reject the password finish.
    # REQ-4.3 / AC-2
    "/api/auth/login",
    # Zitadel Login V2 TOTP finisher — same pre-session rationale as
    # /api/auth/login.
    # REQ-4.3 / AC-2
    "/api/auth/totp-login",
    # pre-session: SSO cookie exchange finaliser. Uses klai_sso cookie, not
    # the BFF csrf_token; pre-session by construction.
    # REQ-1.2 / AC-2
    "/api/auth/sso-complete",
    # signup: new users have no BFF session or csrf_token yet. pre-session.
    # REQ-4.3 / AC-2
    "/api/signup",
    # health probe: liveness/readiness probe, GET-only, no side effects.
    # no session required.
    # REQ-1.2 / AC-7
    "/api/health",
    # no session: reserved prefix for intentionally public endpoints (none in
    # use today). CORS allowlist blocks cross-origin credential probing.
    # REQ-1.2 / AC-7
    "/api/public/",
    # sendBeacon: navigator.sendBeacon cannot set X-CSRF-Token custom headers,
    # so the endpoint is intentionally unauthenticated and CSRF-exempt.
    # REQ-4.1 / AC-7
    "/api/perf",
    # internal: service-to-service surface authenticated by X-Internal-Secret,
    # not the BFF cookie. CSRF is a cookie-based threat.
    # REQ-3.1 / AC-7
    "/internal/",
    # partner: partner API endpoints authenticated by Bearer pk_live_... keys,
    # not the BFF cookie. CSRF is a cookie-based threat.
    # REQ-3.1 / AC-9, AC-11
    "/partner/",
    # /widget/ prefix has NO mounted handlers in portal-api (audited 2026-04-25:
    # grep for prefix="/widget" returns zero results). Removed per REQ-4 audit.
    # Widget traffic now lives under /partner/v1/widget-config (see partner CORS REQ-2).
)


class SessionMiddleware(BaseHTTPMiddleware):
    """Attach `request.state.session` and enforce CSRF when appropriate."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.session = None

        record = await _load_session_from_cookie(request)
        if record is not None:
            context = _to_context(record)
            request.state.session = context
            structlog.contextvars.bind_contextvars(
                user_id=record.zitadel_user_id,
                session_id=record.sid,
            )
            if record.org_id is not None:
                structlog.contextvars.bind_contextvars(org_id=str(record.org_id))

            csrf_failure = _check_csrf(request, record)
            if csrf_failure is not None:
                return csrf_failure

        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_session_from_cookie(request: Request) -> SessionRecord | None:
    """Resolve the session cookie and transparently refresh the access token.

    Returns the (possibly refreshed) session record, or None when the cookie
    is missing/stale/unrefreshable. The middleware does not block on refresh
    failure — it simply presents no session, which causes downstream auth to
    return 401 cookie_required and the SPA to redirect through
    /api/auth/oidc/start for a fresh login.
    """
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return None
    record = await session_service.load(sid)
    if record is None:
        logger.info("bff_session_cookie_stale", sid_prefix=sid[:8])
        return None
    return await session_service.refresh_if_needed(record)


def _to_context(record: SessionRecord) -> SessionContext:
    return SessionContext(
        sid=record.sid,
        zitadel_user_id=record.zitadel_user_id,
        access_token=record.access_token,
        csrf_token=record.csrf_token,
        access_token_expires_at=record.access_token_expires_at,
    )


def _check_csrf(request: Request, record: SessionRecord) -> Response | None:
    """Return a 403 Response when CSRF validation fails, otherwise None."""
    if request.method in _CSRF_SAFE_METHODS:
        return None
    if any(request.url.path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
        return None

    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not header_value or not _secure_equal(header_value, record.csrf_token):
        logger.warning(
            "bff_csrf_check_failed",
            path=request.url.path,
            method=request.method,
            has_header=bool(header_value),
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "csrf_invalid"},
        )
    return None


def _secure_equal(a: str, b: str) -> bool:
    """Constant-time comparison — prevents timing attacks on the CSRF token."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b, strict=False):
        result |= ord(x) ^ ord(y)
    return result == 0
