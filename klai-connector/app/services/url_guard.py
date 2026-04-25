"""Connector-side load-time SSRF gate (SPEC-SEC-SSRF-001 REQ-8.4).

Legacy rows in ``connector.connectors`` may have been persisted before
the portal validators landed (REQ-2 / REQ-8). When a scheduled sync
loads such a row and extracts its config, this module re-validates
the user-supplied URL fields against the canonical klai-libs guard.
A stored ``base_url`` pointing at ``portal-api:8010`` or ``10.0.0.5``
SHALL fail the sync run with a stable error code rather than fetch.

The validator + Atlassian allowlist live in
:mod:`klai_image_storage.url_guard` — this module only adds connector-
specific context (stable error codes, structured log event keys, the
``PersistedUrlRejectedError`` envelope the sync runner catches). No
reject-list logic lives here; drift between portal and connector is
structurally impossible.
"""

from __future__ import annotations

from typing import NoReturn

from klai_image_storage.url_guard import (
    SsrfBlockedError,
    validate_confluence_base_url,
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
            # Suppress the guard's own ``ssrf_blocked`` entry — we
            # emit the richer ``web_crawler_persisted_url_blocked``
            # event in ``_reject`` with connector_id + field context.
            validate_url_pinned_sync(raw, log_as=None)
        except SsrfBlockedError as exc:
            _reject(field, exc.reason, str(exc), exc.hostname)


def validate_confluence_base_url_strict(base_url: str, *, connector_id: str | None = None) -> None:
    """REQ-8.4 / AC-21: re-validate a stored Confluence base_url at load time.

    Delegates the reject-list logic (Atlassian allowlist, SSRF, IP-
    literal classification) to
    :func:`klai_image_storage.url_guard.validate_confluence_base_url`
    so the connector and portal share exactly one implementation.
    This helper adds connector-specific envelopes: stable error code
    (``ssrf_blocked_persisted_confluence_base_url``), structured log
    event (``confluence_base_url_blocked``), and the
    :class:`PersistedUrlRejectedError` the sync runner catches.
    """

    try:
        # Suppress the generic ``ssrf_blocked`` entry — the
        # ``confluence_base_url_blocked`` event below carries the
        # richer connector_id context.
        validate_confluence_base_url(base_url, log_as=None)
    except SsrfBlockedError as exc:
        logger.warning(
            "confluence_base_url_blocked — %s",
            exc,
            extra={
                "event": "confluence_base_url_blocked",
                "url": base_url.split("?", 1)[0],
                "hostname": exc.hostname,
                "reason": exc.reason,
                "connector_id": connector_id,
            },
        )
        raise PersistedUrlRejectedError(
            error_code=SSRF_PERSISTED_CONFLUENCE_ERROR,
            hostname=exc.hostname,
            message=str(exc),
        ) from exc


__all__ = [
    "SSRF_PERSISTED_CONFLUENCE_ERROR",
    "SSRF_PERSISTED_ERROR",
    "PersistedUrlRejectedError",
    "validate_confluence_base_url_strict",
    "validate_web_crawler_config_strict",
]
