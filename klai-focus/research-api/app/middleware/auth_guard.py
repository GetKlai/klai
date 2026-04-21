"""
SPEC-SEC-004: Defense-in-depth auth guard middleware.

Every request (except explicitly exempt paths) MUST carry an Authorization
Bearer header. Actual token validation still happens per-route via
`Depends(get_current_user)` — this middleware is a safety net that rejects
requests with a missing header *before* the route handler runs.

Why this matters: a new route added without the `Depends(get_current_user)`
dependency would otherwise be publicly reachable via the BFF proxy.
The middleware makes the absence of auth a 401, not a 200.
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
    # FastAPI internals (docs are disabled in main.py but defensive)
    "/openapi.json",
    "/docs",
    "/redoc",
)


class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Reject any request without an Authorization header early.

    Token validity is verified downstream by `get_current_user`. This guard
    only checks for *presence* of the header; an invalid token still yields
    401 (from jose.jwt.decode), but a request with no header at all is
    rejected here instead of relying on every route remembering its Depends.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        if request.method == "OPTIONS":
            # CORS preflight — browsers do not attach credentials
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization required"},
            )

        return await call_next(request)
