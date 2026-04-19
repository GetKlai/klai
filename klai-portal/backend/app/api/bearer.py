"""
Session-aware Bearer credentials dependency (SPEC-AUTH-008 Phase A4).

Drop-in replacement for `HTTPBearer()` that returns the same
`HTTPAuthorizationCredentials` object as before, but:

- When the BFF session middleware has attached a `SessionContext` to
  `request.state.session`, we synthesise a Bearer credential from the
  session's access_token. The downstream route sees a standard bearer
  and doesn't need to know the caller authenticated via cookies.

- When no session is present, we fall through to the real HTTPBearer
  extractor and 401 if no Authorization header is supplied.

Effect: every existing `credentials: HTTPAuthorizationCredentials =
Depends(bearer)` call site automatically accepts cookie auth too — no
ripple across the 150+ route handlers.

While `BFF_ENFORCE_COOKIES` is false (the soak period), both paths work.
Flipping it to true will reject bearer requests that don't also carry a
session cookie (Phase D).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.session import SessionContext

# Real HTTPBearer extractor — auto_error=False so a missing header does not
# immediately 403; we want to decide based on whether a session was resolved.
_real_bearer = HTTPBearer(auto_error=False)


async def bearer(
    request: Request,
    real: HTTPAuthorizationCredentials | None = Depends(_real_bearer),
) -> HTTPAuthorizationCredentials:
    """Return either the BFF session's synthesised Bearer or the real one."""
    session = getattr(request.state, "session", None)
    if isinstance(session, SessionContext):
        return HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=session.access_token,
        )

    if settings.bff_enforce_cookies:
        # Phase D flipped this flag — bearer-only requests are no longer
        # accepted. The only way in is via the BFF session cookie.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="cookie_required",
        )

    if real is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return real
