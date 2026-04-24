"""Portal-side SSRF guard (SPEC-SEC-SSRF-001 REQ-4.2 / REQ-8).

Thin re-export layer over :mod:`klai_image_storage.url_guard` — the
canonical guard shared by every klai service that fetches a
user-supplied URL. Keeping a ``from app.services.url_validator import
...`` import surface separate from the klai-libs path means the
portal codebase keeps a consistent service-layer import pattern and
reviewers see exactly one file marking the SSRF boundary.

The previous revision of this module held its own Confluence
allowlist + IP-literal check. Those now live in klai-libs so the
connector's load-time legacy-row gate and the portal's pydantic
validator cannot drift. A single ``ATLASSIAN_ALLOWED_SUFFIXES``
change in one place affects every callsite.
"""

from __future__ import annotations

from klai_image_storage.url_guard import (
    ATLASSIAN_ALLOWED_SUFFIXES,
    PinnedResolverTransport,
    SsrfBlockedError,
    ValidatedURL,
    validate_confluence_base_url,
    validate_url_pinned,
    validate_url_pinned_sync,
)

__all__ = [
    "ATLASSIAN_ALLOWED_SUFFIXES",
    "PinnedResolverTransport",
    "SsrfBlockedError",
    "ValidatedURL",
    "validate_confluence_base_url",
    "validate_confluence_base_url_sync",
    "validate_url_pinned",
    "validate_url_pinned_sync",
]


# Backwards-compatible alias — the previous revision exported
# ``validate_confluence_base_url_sync``. Existing ``connectors.py``
# imports keep working; new call sites should prefer the
# unsuffixed name which is consistent with the shared-lib symbol.
validate_confluence_base_url_sync = validate_confluence_base_url
