"""Structured logging setup for klai-mailer.

SPEC-LOGGING-EXTRACT-001: the canonical setup_logging + middleware +
healthcheck filter live in ``log_utils.structlog_setup``. This module is
a thin compat shim so existing imports keep working:

    from app.logging_setup import RequestContextMiddleware, setup_logging
    from app.logging_setup import _HealthCheckAccessFilter  # tests/

The mailer's own ``setup_logging()`` defaulted ``service_name`` to
``"klai-mailer"`` and the middleware unconditionally rebound it on every
request. We preserve both behaviours via ``_setup_logging`` /
``RequestContextMiddleware`` wrappers.
"""

from __future__ import annotations

from typing import Any

from log_utils.structlog_setup import (
    HealthCheckAccessFilter,
)
from log_utils.structlog_setup import (
    RequestContextMiddleware as _BaseRequestContextMiddleware,
)
from log_utils.structlog_setup import setup_logging as _setup_logging

# Backward-compat alias preserved for klai-mailer/tests/test_logging_setup.py
# which imports the leading-underscore name. Both names resolve to the same class.
_HealthCheckAccessFilter = HealthCheckAccessFilter

# Service name bound to structlog contextvars on startup AND re-bound on every
# request by the middleware below.
_SERVICE_NAME = "klai-mailer"


def setup_logging(service_name: str = _SERVICE_NAME) -> None:
    """Configure structlog with stdlib integration (delegates to log_utils)."""
    _setup_logging(service_name)


class RequestContextMiddleware(_BaseRequestContextMiddleware):
    """Bind trace context from upstream services to structlog for log correlation.

    klai-mailer's original middleware unconditionally rebound
    ``service="klai-mailer"`` on every request. The shared
    ``log_utils.structlog_setup.RequestContextMiddleware`` makes that
    optional via a constructor kwarg; this subclass keeps the original
    behaviour for the mailer.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app, service_name=_SERVICE_NAME)


__all__ = [
    "RequestContextMiddleware",
    "setup_logging",
]
