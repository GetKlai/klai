"""Structured logging setup using structlog."""

import logging
import os
import re
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# SPEC-SEC-HYGIENE-001 REQ-41: hard caps on incoming trace headers so an
# attacker cannot poison every log line with terminal escape sequences,
# HTML tags, or multi-megabyte garbage. Values that fail validation are
# either replaced (X-Request-ID → server UUID) or dropped from the log
# context (X-Org-ID), never propagated verbatim.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ORG_ID_RE = re.compile(r"^[0-9]{1,20}$")


def setup_logging(service_name: str = "retrieval-api") -> None:
    """Configure structlog with stdlib integration.

    Args:
        service_name: The Docker service name, bound as a context variable.
    """
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    structlog.contextvars.bind_contextvars(service=service_name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind trace context from upstream services to structlog for log correlation.

    SPEC-SEC-HYGIENE-001 REQ-41: header values are validated against tight
    regexes before they reach the structlog context. Invalid X-Request-ID
    is replaced with a server-generated UUID; invalid X-Org-ID is dropped
    from the context entirely (not rejected at the HTTP layer, because
    the same header has multiple legitimate origins downstream).
    """

    async def dispatch(self, request: Request, call_next: ...) -> Response:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(service="retrieval-api")

        raw_request_id = request.headers.get("x-request-id") or ""
        request_id = raw_request_id if _REQUEST_ID_RE.match(raw_request_id) else str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        raw_org_id = request.headers.get("x-org-id")
        if raw_org_id and _ORG_ID_RE.match(raw_org_id):
            structlog.contextvars.bind_contextvars(org_id=raw_org_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
