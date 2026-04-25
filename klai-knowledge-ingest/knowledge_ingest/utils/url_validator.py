"""URL validation utilities for SSRF protection.

Thin wrapper over :mod:`klai_image_storage.url_guard`, the canonical
guard shared by every klai service that fetches a user-supplied URL.
This module keeps the historical knowledge-ingest names
(``validate_url``, ``validate_url_scheme``, ``is_private_ip``) so
existing call sites compile without code changes, while new callers
use :func:`validate_url_pinned` to get the :class:`ValidatedURL`
(carries the resolved IP set — see SPEC-SEC-SSRF-001 REQ-3.1).

Pre-SPEC-SEC-SSRF-001 the module did its own scheme / private-IP
checks. Those are now delegated to klai-libs so knowledge-ingest and
klai-connector share one reject-list with no drift.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from klai_image_storage.url_guard import (
    PinnedResolverTransport,
    SsrfBlockedError,
    ValidatedURL,
    classify_ip,
    validate_url_pinned,
    validate_url_pinned_sync,
)

__all__ = [
    "PinnedResolverTransport",
    "SsrfBlockedError",
    "ValidatedURL",
    "classify_ip",
    "is_private_ip",
    "validate_url",
    "validate_url_pinned",
    "validate_url_pinned_sync",
    "validate_url_scheme",
]


def validate_url_scheme(url: str) -> None:
    """Raise ``ValueError`` if *url* is not HTTPS.

    Preserved for callers that want the cheap scheme-only check
    without DNS resolution (e.g. form validation before a queue
    enqueue). For full SSRF protection call :func:`validate_url` or
    :func:`validate_url_pinned`.
    """

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS URLs are allowed. Got: {parsed.scheme!r}")


def is_private_ip(ip: str) -> bool:
    """Return True if *ip* is private, reserved, or unparseable.

    Kept for compatibility — new code SHOULD use
    :func:`klai_image_storage.url_guard.classify_ip` to get the
    specific reason code instead of a boolean.
    """

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # fail closed on unparseable input
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def validate_url(url: str, dns_timeout: float = 2.0) -> str:
    """Validate *url* for SSRF and return it unchanged on success.

    Backwards-compatible wrapper around :func:`validate_url_pinned`.
    Existing callers (``crawl_url`` at ``routes/crawl.py:235``) keep
    working; new callers that need DNS-rebinding protection should
    use :func:`validate_url_pinned` directly and route the subsequent
    HTTP fetch through :class:`PinnedResolverTransport`.

    Raises ``ValueError`` (via :class:`SsrfBlockedError`) on
    rejection — the subclass relationship preserves the behaviour of
    ``except ValueError`` blocks in older code.
    """

    await validate_url_pinned(url, dns_timeout=dns_timeout)
    return url
