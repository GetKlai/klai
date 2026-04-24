"""Portal-side SSRF guard (SPEC-SEC-SSRF-001 REQ-4.2 / REQ-8).

Thin wrapper around :mod:`klai_image_storage.url_guard` — the canonical
guard used by every klai service that fetches a user-supplied URL.
Keeping this module separate from direct klai_image_storage imports
preserves the ``from app.services.url_validator import ...`` import
pattern the portal codebase uses everywhere and makes it obvious
where the SSRF boundary lives for reviewers.

The ``*_sync`` variants exist for pydantic ``model_validator(mode=
"after")`` which runs synchronously in the request-handling path.
"""

from __future__ import annotations

from urllib.parse import urlparse

from klai_image_storage.url_guard import (
    PinnedResolverTransport,
    SsrfBlockedError,
    ValidatedURL,
    validate_url_pinned,
    validate_url_pinned_sync,
)

__all__ = [
    "CONFLUENCE_ALLOWED_SUFFIXES",
    "PinnedResolverTransport",
    "SsrfBlockedError",
    "ValidatedURL",
    "validate_confluence_base_url_sync",
    "validate_url_pinned",
    "validate_url_pinned_sync",
]


# REQ-8.1 / AC-19: Confluence connector ``base_url`` must be on an
# Atlassian-owned domain. Matched case-insensitive against the parsed
# hostname. IP literals are structurally skipped and handled by the
# inner SSRF guard instead (AC-19 bullet 3).
CONFLUENCE_ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".atlassian.net",
    ".atlassian.com",
)


def validate_confluence_base_url_sync(base_url: str) -> ValidatedURL:
    """REQ-8: Confluence-specific domain allowlist + SSRF check.

    Applies, in order:

    1. HTTPS scheme check (REQ-8.2 bullet 1).
    2. Atlassian domain allowlist (REQ-8.1). IP literals skip the
       allowlist and fall through to the SSRF reject-list instead.
    3. Full SSRF reject-list (REQ-8.2 bullet 2) via
       :func:`validate_url_pinned_sync` — RFC1918, loopback,
       link-local, docker-internal, etc.

    Returns the :class:`ValidatedURL` on success so callers can
    reuse the pinned IP for subsequent fetches. Raises
    :class:`SsrfBlockedError` (``ValueError`` subclass) on failure,
    which pydantic surfaces as a 422 error naming the offending
    field.
    """

    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise SsrfBlockedError(
            "base_url must use HTTPS",
            reason="non_https",
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("base_url has no hostname", reason="no_hostname")

    # IP literal? Skip the domain allowlist (an attacker cannot
    # bypass the SSRF guard by using an IP literal — the inner
    # ``validate_url_pinned_sync`` classifies the IP and rejects
    # RFC1918 / reserved / etc.).
    is_literal = all(c.isdigit() or c in ".:" for c in host)
    if not is_literal:
        if not any(host.endswith(suffix) for suffix in CONFLUENCE_ALLOWED_SUFFIXES):
            raise SsrfBlockedError(
                "base_url must be on *.atlassian.net or *.atlassian.com",
                reason="domain_not_allowed",
                hostname=host,
            )

    return validate_url_pinned_sync(base_url)
