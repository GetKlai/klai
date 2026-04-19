"""
Internal secret authentication middleware.

Validates the X-Internal-Secret header on all requests except /health.

SPEC-SEC-011: ``knowledge_ingest_secret`` is a required configuration value —
emptiness is rejected at settings load time, so this middleware never runs
with an unset secret. There is no fail-open branch; the only possible
outcomes are "valid secret → allow" or "invalid/missing secret → 401".
"""
import hmac
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from knowledge_ingest.config import settings


class InternalSecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
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
