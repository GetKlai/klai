"""FastAPI middleware to bind request context to structlog."""

import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class LoggingContextMiddleware(BaseHTTPMiddleware):
    """Bind org_id, user_id and request_id to structlog context for each request.

    Also propagates X-Request-ID as a response header so downstream services
    and clients can correlate logs across the request chain.
    """

    async def dispatch(self, request: Request, call_next: ...) -> Response:
        structlog.contextvars.clear_contextvars()

        # Accept X-Request-ID from upstream (e.g. Caddy) or generate a new one
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)

        # Extract org_id and user_id from request state AFTER call_next,
        # because auth middleware sets these during the route handler.
        org_id = getattr(request.state, "org_id", None)
        user_id = getattr(request.state, "user_id", None)
        if org_id:
            structlog.contextvars.bind_contextvars(org_id=str(org_id))
        if user_id:
            structlog.contextvars.bind_contextvars(user_id=str(user_id))

        # Echo request_id back so clients and load balancers can trace requests
        response.headers["X-Request-ID"] = request_id
        return response
