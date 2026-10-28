"""Structured logging for klai-slack, delegating to log_utils (SPEC-LOGGING-EXTRACT-001)."""

from __future__ import annotations

from typing import Any

from log_utils.structlog_setup import RequestContextMiddleware as _BaseRequestContextMiddleware
from log_utils.structlog_setup import setup_logging as _setup_logging

_SERVICE_NAME = "klai-slack"


def setup_logging(service_name: str = _SERVICE_NAME) -> None:
    _setup_logging(service_name)


class RequestContextMiddleware(_BaseRequestContextMiddleware):
    """Bind X-Request-ID to every log line and re-bind the service name per request."""

    def __init__(self, app: Any) -> None:
        super().__init__(app, service_name=_SERVICE_NAME)


__all__ = ["RequestContextMiddleware", "setup_logging"]
