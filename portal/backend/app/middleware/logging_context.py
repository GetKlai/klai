"""FastAPI middleware to bind request context to structlog."""

import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class LoggingContextMiddleware(BaseHTTPMiddleware):
    """Bind org_id, user_id and request_id to structlog context for each request."""

    async def dispatch(self, request: Request, call_next: ...) -> Response:
        structlog.contextvars.clear_contextvars()
        request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Extract org_id and user_id from request state if set by auth middleware
        # Auth middleware runs before this, so state may already have these
        org_id = getattr(request.state, "org_id", None)
        user_id = getattr(request.state, "user_id", None)
        if org_id:
            structlog.contextvars.bind_contextvars(org_id=str(org_id))
        if user_id:
            structlog.contextvars.bind_contextvars(user_id=str(user_id))

        response = await call_next(request)
        return response
