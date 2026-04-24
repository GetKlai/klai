"""Zitadel webhook signature verification.

Factored out of app/main.py for SPEC-SEC-MAILER-INJECTION-001 REQ-7 (uniform
401 body across failure modes) and REQ-10 (strict parser rejecting unknown
vN fields).

Public surface:
- `verify_zitadel_signature(raw_body, header_value, secret)` — the canonical
  verifier. Raises `SignatureError` on any failure; the exception carries a
  distinct `reason` for observability without leaking it to the HTTP response.
- `SignatureError` — internal-only exception. Callers must catch it and emit
  a single uniform `{"detail": "invalid signature"}` 401 (REQ-7.1).

@MX:ANCHOR: every /notify and /debug caller funnels through this module.
@MX:REASON: centralising the parser + verifier ensures REQ-7 / REQ-10
invariants hold regardless of call site.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

# Zitadel currently emits exactly two fields: t (timestamp) and v1 (HMAC hex).
# Any other key is a parser rejection per REQ-10.1. If Zitadel ships a v2 /
# v3 scheme, this set MUST be updated explicitly in a versioned change.
ALLOWED_SIG_KEYS: frozenset[str] = frozenset({"t", "v1"})

# REQ-10.3: defence against header-splitting / padding attacks. Current Zitadel
# headers are 2 tokens; 5 leaves headroom for future versions.
MAX_SIG_TOKENS: int = 5

# Zitadel replay window.
REPLAY_WINDOW_SECONDS: int = 300


class SignatureError(Exception):
    """Raised when a Zitadel webhook signature fails verification.

    The `reason` attribute MUST be one of the sentinels below. It is logged,
    not returned — see REQ-7.2.
    """

    REASONS = frozenset({
        "missing_header",
        "malformed_header",
        "timestamp_out_of_window",
        "hmac_mismatch",
        "unknown_vN_field",
        "replay",
    })

    def __init__(self, reason: str, *, extra: dict[str, Any] | None = None) -> None:
        if reason not in self.REASONS:
            raise AssertionError(f"unknown signature reason: {reason!r}")
        super().__init__(reason)
        self.reason = reason
        self.extra: dict[str, Any] = extra or {}


def _parse_signature_header(header: str) -> dict[str, str]:
    """Strict parser. Rejects unknown keys and over-long headers.

    Raises `SignatureError` with the appropriate reason.
    """
    tokens = header.split(",")
    if len(tokens) > MAX_SIG_TOKENS:
        raise SignatureError("unknown_vN_field", extra={"unknown_fields": tokens})

    parts: dict[str, str] = {}
    unknown: list[str] = []
    for raw_token in tokens:
        token = raw_token.strip()
        if not token:
            raise SignatureError("malformed_header")
        if "=" not in token:
            raise SignatureError("malformed_header")
        k, v = token.split("=", 1)
        k = k.strip()
        if k not in ALLOWED_SIG_KEYS:
            unknown.append(k)
            continue
        parts[k] = v

    if unknown:
        raise SignatureError("unknown_vN_field", extra={"unknown_fields": unknown})
    if "t" not in parts or "v1" not in parts:
        raise SignatureError("malformed_header")
    return parts


def verify_zitadel_signature(
    raw_body: bytes,
    header_value: str | None,
    secret: str,
    *,
    now: int | None = None,
) -> dict[str, str]:
    """Verify the Zitadel signature header against the raw body.

    Returns the parsed `{"t": ..., "v1": ...}` dict on success (useful for
    nonce tracking in REQ-6). Raises `SignatureError` on any failure.

    `now` override exists for deterministic tests.
    """
    if not header_value:
        raise SignatureError("missing_header")

    parts = _parse_signature_header(header_value)
    timestamp = parts["t"]
    v1 = parts["v1"]

    # Timestamp must be an integer within the replay window.
    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise SignatureError("malformed_header") from exc

    current = now if now is not None else int(time.time())
    if abs(current - ts_int) > REPLAY_WINDOW_SECONDS:
        raise SignatureError("timestamp_out_of_window")

    signed_payload = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, v1):
        raise SignatureError("hmac_mismatch")

    return parts
