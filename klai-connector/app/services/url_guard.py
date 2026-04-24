"""Connector-side load-time SSRF gate (SPEC-SEC-SSRF-001 REQ-8.4).

Legacy rows in ``connector.connectors`` may have been persisted before
the portal validators landed (REQ-2 / REQ-8). When a scheduled sync
loads such a row and extracts its config, this module re-validates
the user-supplied URL fields against the same reject-list. A stored
``base_url`` pointing at ``portal-api:8010`` or ``10.0.0.5`` SHALL
fail the sync run with a stable error code rather than fetch.

Thin wrappers around :mod:`klai_image_storage.url_guard` — one entry
point per connector type so the structlog event key and the
`sync_runs.error` string are stable and LogsQL-queryable (REQ-8.5 /
AC-21).
"""

from __future__ import annotations

from typing import NoReturn
from urllib.parse import urlparse

from klai_image_storage.url_guard import (
    SsrfBlockedError,
    validate_url_pinned_sync,
)

from app.core.logging import get_logger

logger = get_logger(__name__)


# Stable error codes surfaced on ``sync_runs.error`` — treated as
# query keys by both ops dashboards and regression tests. Do not
# rename without updating AC-9 / AC-21 fixtures in lock-step.
SSRF_PERSISTED_ERROR = "ssrf_blocked_persisted_url"
SSRF_PERSISTED_CONFLUENCE_ERROR = "ssrf_blocked_persisted_confluence_base_url"


class PersistedUrlRejectedError(Exception):
    """Raised when a legacy row's URL fails the guard at load time.

    Subclasses plain :class:`Exception` (NOT ``ValueError``) so the
    sync runner can catch it specifically and mark the sync run
    failed with the stable error code — without colliding with the
    generic value-error paths in adapter extraction logic.
    """

    def __init__(self, error_code: str, hostname: str | None, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.hostname = hostname


_ATLASSIAN_SUFFIXES = (".atlassian.net", ".atlassian.com")


def validate_web_crawler_config_strict(
    config: dict[str, object],
    *,
    connector_id: str | None = None,
) -> None:
    """REQ-2.4 / AC-9: re-validate a stored web_crawler config at load time.

    The portal's ``WebcrawlerConfig`` validator (Fase 5) guards new and
    updated rows. Legacy rows persisted before that landed may still
    hold an SSRF-unsafe ``base_url`` or ``canary_url``. This helper is
    called by the connector sync runner BEFORE it delegates to
    knowledge-ingest's ``/ingest/v1/crawl/sync`` endpoint — if
    validation fails, ``crawl_site`` is never invoked and the sync
    run is marked failed with the stable error code
    ``ssrf_blocked_persisted_url``.
    """

    def _reject(field: str, reason: str, message: str, hostname: str | None) -> NoReturn:
        logger.warning(
            "web_crawler_persisted_url_blocked — %s",
            message,
            extra={
                "event": "web_crawler_persisted_url_blocked",
                "field": field,
                "hostname": hostname,
                "reason": reason,
                "connector_id": connector_id,
            },
        )
        raise PersistedUrlRejectedError(
            error_code=SSRF_PERSISTED_ERROR,
            hostname=hostname,
            message=f"{field}: {message}",
        )

    for field in ("base_url", "canary_url"):
        raw = config.get(field)
        if raw is None:
            continue
        if not isinstance(raw, str):
            _reject(field, "invalid_type", f"{field} must be a string", None)
        try:
            validate_url_pinned_sync(raw)
        except SsrfBlockedError as exc:
            _reject(field, exc.reason, str(exc), exc.hostname)


def validate_confluence_base_url_strict(base_url: str, *, connector_id: str | None = None) -> None:
    """REQ-8.4 / AC-21: re-validate a stored Confluence base_url at load time.

    Applies the same allowlist + SSRF reject-list as the portal
    ``ConfluenceConfig`` validator. On failure emits an
    ``event="confluence_base_url_blocked"`` warning (REQ-8.5) with
    the stable connector_id / reason fields and raises
    :class:`PersistedUrlRejectedError` — which the sync runner catches
    and converts into a failed ``sync_runs`` row.
    """

    parsed = urlparse(base_url)
    scheme = parsed.scheme
    host = (parsed.hostname or "").lower()

    def _reject(reason: str, message: str) -> None:
        logger.warning(
            "confluence_base_url_blocked — %s",
            message,
            extra={
                "event": "confluence_base_url_blocked",
                "url": base_url.split("?", 1)[0],
                "hostname": host or None,
                "reason": reason,
                "connector_id": connector_id,
            },
        )
        raise PersistedUrlRejectedError(
            error_code=SSRF_PERSISTED_CONFLUENCE_ERROR,
            hostname=host or None,
            message=message,
        )

    if scheme != "https":
        _reject("non_https", "base_url must use HTTPS")
    if not host:
        _reject("no_hostname", "base_url has no hostname")
    # IP literals fall through to the SSRF reject-list.
    is_literal = all(c.isdigit() or c in ".:" for c in host)
    if not is_literal and not any(host.endswith(s) for s in _ATLASSIAN_SUFFIXES):
        _reject(
            "domain_not_allowed",
            "base_url must be on *.atlassian.net or *.atlassian.com",
        )
    try:
        validate_url_pinned_sync(base_url)
    except SsrfBlockedError as exc:
        _reject(exc.reason, str(exc))


__all__ = [
    "SSRF_PERSISTED_CONFLUENCE_ERROR",
    "SSRF_PERSISTED_ERROR",
    "PersistedUrlRejectedError",
    "validate_confluence_base_url_strict",
    "validate_web_crawler_config_strict",
]
