"""
SPEC-SEC-004: Defense-in-depth auth guard middleware for scribe-api.

Every request (except explicitly exempt paths) MUST carry an Authorization
Bearer header. Actual token validation still happens per-route via
`Depends(get_current_user_id)` — this middleware is a safety net that
rejects requests with a missing header *before* the route handler runs.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/v1/health",
    }
)

_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/openapi.json",
    "/docs",
    "/redoc",
)


class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Reject any request without an Authorization header early.

    Token validity is verified downstream by `get_current_user_id`. This
    guard only checks for *presence* of the header.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization required"},
            )

        return await call_next(request)
