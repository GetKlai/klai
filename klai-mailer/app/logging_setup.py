"""Structured logging setup using structlog."""

import logging
import os
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Paths that the access log SHALL drop. Healthcheck spam from Docker's
# liveness probe (every ~10s) drowns the signal of real /notify and
# /internal/send requests. Route paths must match `request_line` byte-for-byte
# as uvicorn formats them: ``GET /health HTTP/1.1`` (no host, no query).
_ACCESS_LOG_FILTERED_PATHS: frozenset[str] = frozenset({"/health"})


class _HealthCheckAccessFilter(logging.Filter):
    """Drop uvicorn access log records for healthcheck endpoints.

    SPEC-SEC-MAILER-INJECTION-001 v0.3.2 follow-up: yesterday's /notify
    500 outage was four-times longer to diagnose than necessary because the
    mailer suppressed ``uvicorn.access`` at WARNING level — request
    lines never appeared in ``docker logs``. Re-enabling INFO shows the
    real request flow but also surfaces every Docker healthcheck. This
    filter removes the noise without removing the signal.

    The filter inspects ``record.args`` (the tuple uvicorn passes to
    its ``%s "%s %s HTTP/%s" %d`` format string) so it works regardless
    of the eventual rendered message. Defensive against changes to
    uvicorn's access-log format: if ``args`` is missing or doesn't
    match the expected shape, the record passes through (we'd rather
    leak a healthcheck line than swallow a real request log).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        # uvicorn access record args: (client_addr, method, full_path, http_version, status_code)
        full_path = args[2]
        if not isinstance(full_path, str):
            return True
        # full_path looks like "/health" or "/health?check=1"; split on '?'
        path_only = full_path.split("?", 1)[0]
        return path_only not in _ACCESS_LOG_FILTERED_PATHS


def setup_logging(service_name: str = "klai-mailer") -> None:
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

    # uvicorn.access at INFO so every request is visible in `docker logs`
    # — the diagnostic signal that was missing during the 2026-04-29 mailer
    # /notify 500 outage. Spam from Docker healthcheck is filtered out via
    # _HealthCheckAccessFilter so signal-to-noise stays good.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.INFO)
    access_logger.addFilter(_HealthCheckAccessFilter())
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    structlog.contextvars.bind_contextvars(service=service_name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind trace context from upstream services to structlog for log correlation."""

    async def dispatch(self, request: Request, call_next: ...) -> Response:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(service="klai-mailer")

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        if org_id := request.headers.get("x-org-id"):
            structlog.contextvars.bind_contextvars(org_id=org_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
