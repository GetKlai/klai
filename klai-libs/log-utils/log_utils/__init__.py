"""Shared log/secret utilities for Klai Python services.

SPEC-SEC-INTERNAL-001 v0.3.0:
- REQ-1.7  verify_shared_secret  -- constant-time inbound-secret compare
- REQ-4.1  sanitize_response_body -- strip secret substrings from upstream bodies
- REQ-4.2  extract_secret_values  -- pydantic-Settings introspection
- REQ-4.4  sanitize_from_settings -- convenience wrapper

SPEC-LOGGING-EXTRACT-001:
- setup_logging              -- canonical structlog + stdlib setup
- HealthCheckAccessFilter    -- drop /health uvicorn access lines
- RequestContextMiddleware   -- bind X-Request-ID / X-Org-ID to logs
"""

from .sanitize import sanitize_from_settings, sanitize_response_body
from .secret_compare import verify_shared_secret
from .settings_scan import extract_secret_values
from .structlog_setup import (
    DEFAULT_HEALTHCHECK_PATHS,
    DEFAULT_THIRD_PARTY_LEVELS,
    HealthCheckAccessFilter,
    RequestContextMiddleware,
    setup_logging,
)

__all__ = [
    "DEFAULT_HEALTHCHECK_PATHS",
    "DEFAULT_THIRD_PARTY_LEVELS",
    "HealthCheckAccessFilter",
    "RequestContextMiddleware",
    "extract_secret_values",
    "sanitize_from_settings",
    "sanitize_response_body",
    "setup_logging",
    "verify_shared_secret",
]
