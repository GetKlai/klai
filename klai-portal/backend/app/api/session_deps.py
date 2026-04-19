"""
FastAPI dependencies for the BFF session (SPEC-AUTH-008).

`get_session` returns the resolved :class:`~app.core.session.SessionContext`
or raises 401 cookie_required. The error detail matches the session-aware
bearer shim so the SPA has a single code path for "you need to log in".
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.core.session import SessionContext


def get_session(request: Request) -> SessionContext:
    """Require a valid cookie-based session; raise 401 cookie_required otherwise."""
    session = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="cookie_required",
        )
    return session


def get_optional_session(request: Request) -> SessionContext | None:
    """Return the session if present, or None — for endpoints with mixed auth."""
    session = getattr(request.state, "session", None)
    return session if isinstance(session, SessionContext) else None
