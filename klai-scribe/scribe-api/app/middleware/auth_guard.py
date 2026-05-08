"""
SPEC-SEC-004 + SPEC-SEC-IDENTITY-ASSERT-002 REQ-3: Defense-in-depth
auth guard middleware for scribe-api.

After SPEC-002 scribe-api is BFF-only: every request (except explicitly
exempt paths) MUST carry an ``X-Internal-Secret`` header. The full
identity check lives in ``Depends(get_authenticated_caller)`` — which
constant-time-compares the secret AND requires the
``X-Klai-Verified-*`` headers. This middleware is a safety net that
rejects requests without the secret-header *before* the route handler
runs.
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
    """Reject any request without ``X-Internal-Secret`` early.

    Secret value validation (constant-time compare) and the
    ``X-Klai-Verified-*`` header presence check are performed downstream
    by :func:`app.core.auth.get_authenticated_caller`. This guard only
    checks for *presence* of ``X-Internal-Secret``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        if not request.headers.get("x-internal-secret"):
            return JSONResponse(
                status_code=401,
                content={"detail": "unauthenticated"},
            )

        return await call_next(request)
