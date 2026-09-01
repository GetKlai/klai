"""Structured logging setup for knowledge-ingest using structlog."""

import logging
import os
import re
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REDACTED = "<redacted>"
_CONTENT_FIELD_NAMES = frozenset(
    {
        "body_text",
        "content_body",
        "document_text",
        "page_content",
        "raw_content",
    }
)
_CONTENT_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>['\"]?(?:{'|'.join(sorted(_CONTENT_FIELD_NAMES))})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?:\\.|(?!(?P=quote)).)*(?P=quote)",
    re.DOTALL,
)


def _redact_content_assignments(value: str) -> str:
    if not any(field in value for field in _CONTENT_FIELD_NAMES):
        return value

    def _replacement(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{_REDACTED}{quote}"

    return _CONTENT_ASSIGNMENT_RE.sub(_replacement, value)


def _redact_nested_content(value: object) -> object:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if isinstance(key, str) and key.casefold() in _CONTENT_FIELD_NAMES:
                value[key] = _REDACTED
            else:
                value[key] = _redact_nested_content(nested_value)
        return value
    if isinstance(value, list):
        for index, nested_value in enumerate(value):
            value[index] = _redact_nested_content(nested_value)
        return value
    if isinstance(value, tuple):
        return tuple(_redact_nested_content(item) for item in value)
    if isinstance(value, str):
        return _redact_content_assignments(value)
    return value


def redact_content_fields(
    _logger: object, _method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """Redact document bodies from structured fields and rendered task messages."""
    _redact_nested_content(event_dict)
    return event_dict


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
        redact_content_fields,
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
