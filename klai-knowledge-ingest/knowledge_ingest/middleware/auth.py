"""
Internal secret authentication middleware.

Validates the X-Internal-Secret header on all requests except /health and the
Gitea push webhook. The Gitea endpoint has its own HMAC check because Gitea
cannot send Klai's internal service secret.

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

_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/ingest/v1/webhook/gitea",
    }
)


class InternalSecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get("x-internal-secret", "")
        if not provided or not hmac.compare_digest(provided, settings.knowledge_ingest_secret):
            return Response(
                content=json.dumps({"detail": "Invalid or missing X-Internal-Secret"}),
                status_code=401,
                media_type="application/json",
            )

        return await call_next(request)
