"""Constant-time shared-secret comparison.

SPEC-SEC-INTERNAL-001 REQ-1.7.
"""

from __future__ import annotations

import hmac


def verify_shared_secret(header_value: str | None, configured: str) -> bool:
    """Return ``True`` iff ``header_value`` matches ``configured`` in constant time.

    Args:
        header_value: Raw header value from the inbound request. ``None``
            and the empty string are valid inputs and always return
            ``False``.
        configured: The non-empty configured secret. An empty value is a
            misconfiguration and raises ``ValueError`` so callers cannot
            inadvertently authenticate empty headers.

    Raises:
        ValueError: ``configured`` is empty.
    """
    if not configured:
        raise ValueError("verify_shared_secret called with empty configured secret")

    if not header_value:
        # Comparing a non-empty configured secret against an empty header
        # is always False -- but we still call compare_digest on equal-length
        # buffers so the timing channel does not leak the configured length.
        equal_length_dummy = "\x00" * len(configured)
        hmac.compare_digest(
            equal_length_dummy.encode("utf-8"),
            configured.encode("utf-8"),
        )
        return False

    return hmac.compare_digest(
        header_value.encode("utf-8"),
        configured.encode("utf-8"),
    )
