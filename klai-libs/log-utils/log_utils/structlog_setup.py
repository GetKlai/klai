"""Shared structlog setup + ASGI request-context middleware for Klai services.

SPEC-LOGGING-EXTRACT-001 — extracted from ``klai-mailer/app/logging_setup.py``
(the canonical with ``_HealthCheckAccessFilter``).

Public surface:

- :func:`setup_logging` — configure structlog + stdlib logging, route
  ``uvicorn.access`` at INFO with a healthcheck filter, and bind
  ``service`` as a contextvar so every log line carries it.
- :class:`HealthCheckAccessFilter` — drops uvicorn access lines for paths
  that you treat as healthchecks (default: ``/health``). Defensive against
  uvicorn access-record format changes: if ``record.args`` doesn't match
  the expected shape the record passes through.
- :class:`RequestContextMiddleware` — Starlette middleware that binds
  ``request_id`` (from ``X-Request-ID`` header set by Caddy, or a fresh
  UUID) and optionally ``org_id`` / ``user_id`` for downstream log
  correlation. Echoes ``X-Request-ID`` back to the client for browser
  trace parity.

Why "extract not refactor": the mailer's 2026-04-29 /notify outage proved
the value of routing ``uvicorn.access`` at INFO + filtering healthcheck
spam. Each service that re-implements the same pattern slightly
differently is one config-drift away from re-living that outage. This
module is the single source of truth.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Healthcheck access-log filter
# ---------------------------------------------------------------------------

# Paths that uvicorn.access SHALL drop. Healthcheck spam from Docker's
# liveness probe (every ~10s) drowns the signal of real request lines.
# Route paths must match ``request_line`` byte-for-byte as uvicorn formats
# them: ``GET /health HTTP/1.1`` (no host, no query).
DEFAULT_HEALTHCHECK_PATHS: frozenset[str] = frozenset({"/health"})


class HealthCheckAccessFilter(logging.Filter):
    """Drop uvicorn access-log records for healthcheck endpoints.

    Inspects ``record.args`` (the tuple uvicorn passes to its
    ``%s "%s %s HTTP/%s" %d`` format string). Args layout:
    ``(client_addr, method, full_path, http_version, status_code)``.

    Defensive: if ``args`` is missing or doesn't match the expected
    shape, the record passes through. Better to leak a healthcheck line
    than swallow a real request log on a future uvicorn change.
    """

    def __init__(self, paths: Iterable[str] = DEFAULT_HEALTHCHECK_PATHS) -> None:
        super().__init__()
        self._paths: frozenset[str] = frozenset(paths)

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        full_path = args[2]
        if not isinstance(full_path, str):
            return True
        # full_path looks like "/health" or "/health?check=1"; split on '?'
        path_only = full_path.split("?", 1)[0]
        return path_only not in self._paths


# Backward-compat private alias for code that imports the leading-underscore name
# (e.g. klai-mailer/tests/test_logging_setup.py and the original mailer module).
_HealthCheckAccessFilter = HealthCheckAccessFilter


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

# Default third-party loggers that get muted to WARNING. Each service can
# extend or override via ``setup_logging(third_party_levels=...)``.
DEFAULT_THIRD_PARTY_LEVELS: dict[str, int] = {
    "sqlalchemy.engine": logging.WARNING,
    "httpx": logging.WARNING,
}


def setup_logging(
    service_name: str,
    *,
    healthcheck_paths: Iterable[str] = DEFAULT_HEALTHCHECK_PATHS,
    third_party_levels: dict[str, int] | None = None,
    extra_processors: list[structlog.types.Processor] | None = None,
) -> None:
    """Configure structlog with stdlib integration.

    Sets up the canonical Klai logging pipeline:

    - structlog routed through ``stdlib.ProcessorFormatter`` so every
      logger (structlog AND stdlib) renders through the same chain.
    - Root logger at INFO writing to stdout (which Docker / Alloy tail).
    - ``uvicorn.access`` at INFO so every request appears in
      ``docker logs`` (lesson learnt the hard way during the 2026-04-29
      mailer outage). Healthcheck spam is filtered via
      :class:`HealthCheckAccessFilter`.
    - Noisy third-party loggers muted to WARNING.
    - ``service`` bound as a structlog contextvar.

    Args:
        service_name: The Docker / compose service name. Bound as a
            structlog contextvar so every log line carries it. Required.
        healthcheck_paths: Iterable of route paths whose access-log
            records the filter SHALL drop. Defaults to ``{"/health"}``.
        third_party_levels: Mapping of logger-name -> level for
            third-party loggers that should be muted. Defaults to
            ``{"sqlalchemy.engine": WARNING, "httpx": WARNING}``. Pass
            an explicit dict to add/override (e.g. ``{"redis": WARNING}``).
        extra_processors: Optional structlog processors prepended to the
            shared chain (e.g. portal-api's ``mask_secret_str``).
    """
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        *(extra_processors or []),
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
    # /notify 500 outage. Spam from Docker healthcheck is filtered via the
    # access-record filter so signal-to-noise stays good.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.INFO)
    access_logger.addFilter(HealthCheckAccessFilter(paths=healthcheck_paths))

    levels = dict(DEFAULT_THIRD_PARTY_LEVELS)
    if third_party_levels is not None:
        levels.update(third_party_levels)
    for logger_name, level in levels.items():
        logging.getLogger(logger_name).setLevel(level)

    structlog.contextvars.bind_contextvars(service=service_name)


# ---------------------------------------------------------------------------
# RequestContextMiddleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind upstream trace context to structlog for log correlation.

    Reads ``X-Request-ID`` set by Caddy (or generates a UUID4 if absent),
    binds it as a structlog contextvar so every log line carries
    ``request_id``, optionally also binds ``org_id`` (from ``X-Org-ID``)
    and ``user_id`` (from ``X-User-ID``), then echoes ``X-Request-ID``
    back to the client.

    Use after :func:`setup_logging` so the ``service`` contextvar set by
    setup_logging is preserved.

    Args:
        app: The ASGI app this middleware wraps (passed by Starlette).
        service_name: Optional. If provided, the middleware re-binds the
            ``service`` contextvar on every request — useful when the
            service name might be cleared by other middleware. If ``None``
            (the default), the contextvar set by :func:`setup_logging` is
            preserved.
        bind_user_id: If ``True``, bind ``user_id`` from ``X-User-ID``
            header when present. Default ``False`` (most services don't
            have a tenant-scoped user header in their inbound flow).
    """

    def __init__(
        self,
        app: Any,
        *,
        service_name: str | None = None,
        bind_user_id: bool = False,
    ) -> None:
        super().__init__(app)
        self._service_name = service_name
        self._bind_user_id = bind_user_id

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        if self._service_name is not None:
            structlog.contextvars.bind_contextvars(service=self._service_name)

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        if org_id := request.headers.get("x-org-id"):
            structlog.contextvars.bind_contextvars(org_id=org_id)
        if self._bind_user_id and (user_id := request.headers.get("x-user-id")):
            structlog.contextvars.bind_contextvars(user_id=user_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


__all__ = [
    "DEFAULT_HEALTHCHECK_PATHS",
    "DEFAULT_THIRD_PARTY_LEVELS",
    "HealthCheckAccessFilter",
    "RequestContextMiddleware",
    "setup_logging",
]
