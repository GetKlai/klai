"""
Internal secret authentication middleware.

Validates the X-Internal-Secret header on all requests except /health.
If knowledge_ingest_secret is empty, authentication is skipped (backward compat).
"""
import hmac
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from knowledge_ingest.config import settings


class InternalSecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth if secret is not configured (gradual rollout)
        if not settings.knowledge_ingest_secret:
            return await call_next(request)

        # Exempt health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        provided = request.headers.get("x-internal-secret", "")
        if not provided or not hmac.compare_digest(provided, settings.knowledge_ingest_secret):
            return Response(
                content=json.dumps({"detail": "Invalid or missing X-Internal-Secret"}),
                status_code=401,
                media_type="application/json",
            )

        return await call_next(request)
