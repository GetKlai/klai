"""
FastAPI dependencies for the BFF session (SPEC-AUTH-008).

`get_session` is the successor to `Depends(bearer)`. Routes that need an
authenticated caller depend on this; it returns the resolved
:class:`~app.core.session.SessionContext` or raises 401.

During the migration soak, routes may still fall back to the legacy bearer
flow via `api.dependencies._get_caller_org`. This module does not touch the
legacy path — it's the forward-looking surface for new and migrated routes.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.core.session import SessionContext


def get_session(request: Request) -> SessionContext:
    """Require a valid cookie-based session; raise 401 otherwise."""
    session = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no_session",
        )
    return session


def get_optional_session(request: Request) -> SessionContext | None:
    """Return the session if present, or None — for endpoints with mixed auth."""
    session = getattr(request.state, "session", None)
    return session if isinstance(session, SessionContext) else None
