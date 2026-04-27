"""Structlog event emission for identity-assertion calls.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-7.5: every consumer call SHALL emit a
structlog entry with stable key ``event="identity_assert_call"`` and fields
``caller_service``, ``verified``, ``cached``, ``latency_ms``, plus ``reason``
on deny.

This per-service telemetry is independent of the portal-api
``identity_verify_decision`` log (REQ-1.7); the two together let operators
compute consumer-side cache hit rate and detect portal load anomalies.

Privacy: claimed identity values (user_id) are never logged in clear. They
are SHA-256 hashed and truncated, matching the ``_hash_sub`` pattern in
``klai-retrieval-api/retrieval_api/middleware/auth.py``.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Generator

    from klai_identity_assert.models import VerifyResult

_logger = structlog.get_logger("klai_identity_assert")


def hash_user_id(user_id: str) -> str:
    """Return a 16-hex-char prefix of SHA-256 — same convention as retrieval-api."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


@contextmanager
def measure_latency() -> Generator[dict[str, float], None, None]:
    """Yield a dict that gets ``latency_ms`` populated when the block exits.

    Using a dict-as-out-param keeps the call sites flat — no separate
    timer object to thread through. ``latency_ms`` reflects elapsed wall
    time including network I/O.
    """

    holder: dict[str, float] = {"latency_ms": 0.0}
    started = time.monotonic()
    try:
        yield holder
    finally:
        holder["latency_ms"] = (time.monotonic() - started) * 1000.0


def emit_call(
    *,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    result: VerifyResult,
    latency_ms: float,
) -> None:
    """Emit one ``identity_assert_call`` structlog event.

    Logged at info level on allow (operator-visible cache hit rate), warning
    on deny (security-relevant decision). Network errors are surfaced via
    ``result.reason="portal_unreachable"`` and also log at warning.
    """

    fields: dict[str, object] = {
        "caller_service": caller_service,
        "claimed_user_id_hash": hash_user_id(claimed_user_id),
        "claimed_org_id": claimed_org_id,
        "verified": result.verified,
        "cached": result.cached,
        "latency_ms": round(latency_ms, 2),
    }
    if result.evidence is not None:
        fields["evidence"] = result.evidence
    if result.reason is not None:
        fields["reason"] = result.reason

    if result.verified:
        _logger.info("identity_assert_call", **fields)
    else:
        _logger.warning("identity_assert_call", **fields)
