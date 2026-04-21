"""Structured logging setup for portal-api using structlog."""

import logging
import os
import sys
from typing import Any

import structlog
from pydantic import SecretStr


def mask_secret_str(
    logger: Any,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Replace any top-level SecretStr values in the event dict with '***'.

    This processor prevents accidental credential leakage in structured logs.
    It only inspects top-level values (structlog convention).
    """
    for key, value in event_dict.items():
        if isinstance(value, SecretStr):
            event_dict[key] = "***"
    return event_dict


def setup_logging(service_name: str = "portal-api") -> None:
    """Configure structlog with stdlib integration.

    Args:
        service_name: The Docker service name, bound as a context variable.
    """
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        mask_secret_str,
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

    # structlog loggers hand off to ProcessorFormatter before rendering
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

    # Route ALL stdlib loggers (logging.getLogger) through the same processor chain
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

    # Bind service context variable for all logs
    structlog.contextvars.bind_contextvars(service=service_name)
