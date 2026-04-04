"""Structured logging setup for knowledge-ingest using structlog."""

import logging
import os
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def setup_logging(service_name: str = "knowledge-ingest") -> None:
    """Configure structlog with stdlib integration.

    All loggers (structlog + stdlib) emit JSON lines to stdout.
    Alloy collects these and ships them to VictoriaLogs.

    Args:
        service_name: Docker service name, bound as 'service' in every log line.
    """
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.PositionalArgumentsFormatter(),
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

    # Route ALL stdlib loggers through the same processor chain
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

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Bind service name to every log line
    structlog.contextvars.bind_contextvars(service=service_name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind trace context from upstream services to structlog for log correlation."""

    async def dispatch(self, request: Request, call_next: ...) -> Response:
        structlog.contextvars.clear_contextvars()
        # Re-bind service name after clear (set during setup_logging)
        structlog.contextvars.bind_contextvars(service="knowledge-ingest")

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        if org_id := request.headers.get("x-org-id"):
            structlog.contextvars.bind_contextvars(org_id=org_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
