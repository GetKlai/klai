"""Test helper: build a valid Zitadel-signed webhook header.

Used by test_notify_*.py to construct legitimate AND forged signatures
without duplicating the HMAC plumbing.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(body: bytes, secret: str, timestamp: int | None = None) -> tuple[str, int]:
    """Return (header_value, timestamp). Header format: `t=<ts>,v1=<hex>`."""
    ts = timestamp if timestamp is not None else int(time.time())
    payload = f"{ts}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}", ts


def sign_with_extra(body: bytes, secret: str, extra: str, timestamp: int | None = None) -> str:
    """Valid t/v1 pair plus an attacker-supplied extra field (e.g. `v2=x`).

    REQ-10.1 says this MUST be rejected.
    """
    base, _ = sign(body, secret, timestamp=timestamp)
    return f"{base},{extra}"
