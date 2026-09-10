"""Sanitize upstream HTTP response bodies before logging or persisting them.

SPEC-SEC-INTERNAL-001 REQ-4.1 -- REQ-4.6, REQ-10.

Order of operations: scan-and-replace every known secret value first, THEN
truncate to ``max_len``. The SPEC numbered list reads truncate-then-strip;
we deliberately invert that so a secret straddling the truncation boundary
cannot leave a partial tail visible in the returned string. The same
acceptance tests pass either way; this is the safer ordering.

A 64 KiB hard cap on the input bounds the pathological-body DoS surface --
above that, the body is clipped before scanning.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import structlog

from .settings_scan import extract_secret_values

_REDACTED = "<redacted>"
_MAX_INPUT_BYTES = 65_536  # cap before scanning
_MIN_REDACTABLE_LEN = 8  # mirrors REQ-4.2 / settings_scan minimum

_logger = structlog.get_logger("log_utils.sanitize")


def _extract_body(exc_or_response: object) -> str:
    """Pull a string body off an httpx exception or response, duck-typed."""
    if exc_or_response is None:
        return ""
    # httpx.HTTPStatusError exposes .response; raw httpx.Response IS the response.
    candidate: Any = getattr(exc_or_response, "response", None) or exc_or_response
    text = getattr(candidate, "text", None)
    if not isinstance(text, str):
        return ""
    return text


def _redact(body: str, secrets: list[str]) -> tuple[str, int]:
    """Replace every secret in ``body``, whatever escaping it arrived in.

    Upstream bodies are JSON, and JSON has more than one valid way to write
    the same string: ``/`` may be ``\\/``, a newline may be ``\\n`` or
    ``\\u000a``, and a nested payload may be encoded twice. Enumerating
    those forms is a losing game -- one was missed the first time this was
    fixed. So a body that parses as JSON is decoded first and the secrets
    are matched against the DECODED strings, where no escaping exists at
    all; decoding also unwraps a double-encoded payload into a plain string
    that the same literal match then covers.

    A body that is not JSON falls back to literal replacement, which is what
    it always was.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return _redact_literal(body, secrets)

    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, str):
            cleaned, hits = _redact_literal(node, secrets)
            count += hits
            return cleaned
        if isinstance(node, dict):
            return {walk(key): walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    try:
        redacted = json.dumps(walk(parsed))
    except (TypeError, ValueError, RecursionError):
        # Something in there does not round-trip; the literal pass is still
        # better than returning the body untouched.
        return _redact_literal(body, secrets)
    return redacted, count


def _redact_literal(text: str, secrets: list[str]) -> tuple[str, int]:
    """Straight substring replacement, secrets already ordered longest first."""
    count = 0
    for secret in secrets:
        occurrences = text.count(secret)
        if occurrences:
            text = text.replace(secret, _REDACTED)
            count += occurrences
    return text, count


def sanitize_response_body(
    exc_or_response: object,
    secret_values: Iterable[str] | None = None,
    *,
    max_len: int = 512,
) -> str:
    """Return a body string that is safe to log.

    Args:
        exc_or_response: ``httpx.HTTPStatusError``, ``httpx.Response``, or
            any object exposing ``.text`` (or ``.response.text``). ``None``
            and objects without a string body return the empty string.
        secret_values: Iterable of non-empty secret strings to scrub.
            Values shorter than 8 characters are silently skipped to avoid
            over-redaction of common substrings.
        max_len: Maximum returned length (default 512, per REQ-4.1).

    Returns:
        Empty string when the body is missing or empty. Otherwise the
        sanitized, truncated body. When at least one redaction happens
        a structlog ``response_body_sanitized`` debug entry is emitted.
    """
    body = _extract_body(exc_or_response)
    if not body:
        return ""

    # Bound DoS surface before we start scanning.
    if len(body) > _MAX_INPUT_BYTES:
        body = body[:_MAX_INPUT_BYTES]

    original_length = len(body)
    redaction_count = 0

    if secret_values:
        # Longest first, so a shorter secret that happens to be a substring
        # of a longer one cannot corrupt the longer match.
        secrets = sorted(
            (
                value
                for value in _dedupe_strings(secret_values)
                if value and len(value) >= _MIN_REDACTABLE_LEN
            ),
            key=len,
            reverse=True,
        )
        body, redaction_count = _redact(body, secrets)

    truncated = body[:max_len]

    if redaction_count > 0:
        _logger.debug(
            "response_body_sanitized",
            redaction_count=redaction_count,
            original_length=original_length,
        )

    return truncated


def sanitize_from_settings(
    settings_obj: object,
    exc_or_response: object,
    *,
    max_len: int = 512,
) -> str:
    """Convenience wrapper combining settings introspection + sanitization.

    Equivalent to::

        sanitize_response_body(
            exc_or_response,
            extract_secret_values(settings_obj),
            max_len=max_len,
        )
    """
    secrets = extract_secret_values(settings_obj)
    return sanitize_response_body(
        exc_or_response,
        secrets,
        max_len=max_len,
    )


def _dedupe_strings(values: Iterable[str]) -> set[str]:
    """De-duplicate; tolerate accidental ``None`` entries from sloppy callers."""
    return {v for v in values if v}
