"""
Cookie-session Bearer credentials dependency (SPEC-AUTH-008 Phase E).

Every `Depends(bearer)` call site gets an `HTTPAuthorizationCredentials`
synthesised from the BFF session cookie. If the SessionMiddleware did
not resolve a session on the current request, the dependency returns
401 — Bearer headers alone are no longer accepted.

This is the post-migration version. It replaces the Phase A2..A4 shim
that tolerated legacy Bearer headers during the cookie-auth soak.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.session import SessionContext


async def bearer(request: Request) -> HTTPAuthorizationCredentials:
    """Synthesise Bearer credentials from the BFF session on request.state."""
    session = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="cookie_required",
        )
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=session.access_token,
    )
