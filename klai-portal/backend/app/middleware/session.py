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

import time

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

# Route prefixes that intentionally operate without a session — e.g. the OIDC
# flow itself, which is what creates the session. These are always CSRF-exempt.
_CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
    "/api/auth/idp-intent",
    "/api/auth/idp-callback",
    "/api/signup",
    "/api/health",
    "/api/public/",
    "/internal/",
    "/partner/",
    "/widget/",
)


class SessionMiddleware(BaseHTTPMiddleware):
    """Attach `request.state.session` and enforce CSRF when appropriate."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
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
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return None
    record = await session_service.load(sid)
    if record is None:
        logger.info("bff_session_cookie_stale", sid_prefix=sid[:8])
        return None
    if record.access_token_expires_at < int(time.time()):
        # Access token is stale — leave handling to Phase A3 (refresh endpoint).
        # For A2 we still surface the session; downstream Zitadel calls will 401
        # and the frontend will trigger a refresh. This keeps the middleware
        # narrow and unit-testable without a live Zitadel client.
        logger.debug("bff_session_access_token_expired", sid_prefix=sid[:8])
    return record


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
