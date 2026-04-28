"""Tests for telemetry helpers.

structlog uses its own pipeline rather than stdlib logging by default, so
``caplog`` does not see emitted events. We use structlog's built-in
``capture_logs`` context manager — the idiomatic way to assert on
structured events emitted via ``BoundLoggerBase``.
"""

from __future__ import annotations

from structlog.testing import capture_logs

from klai_identity_assert import VerifyResult
from klai_identity_assert.telemetry import emit_call, hash_user_id, measure_latency


def test_hash_user_id_is_stable_and_truncated() -> None:
    a = hash_user_id("user-1")
    b = hash_user_id("user-1")
    c = hash_user_id("user-2")

    assert a == b
    assert a != c
    assert len(a) == 16


def test_measure_latency_populates_field() -> None:
    with measure_latency() as latency:
        # Trivial work — just confirm the timer runs.
        _ = sum(range(100))
    assert latency["latency_ms"] >= 0.0


def test_emit_call_logs_event_with_required_fields_on_allow() -> None:
    result = VerifyResult.allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")

    with capture_logs() as captured:
        emit_call(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            result=result,
            latency_ms=12.34,
        )

    assert len(captured) == 1
    entry = captured[0]
    assert entry["event"] == "identity_assert_call"
    assert entry["log_level"] == "info"
    assert entry["caller_service"] == "scribe"
    assert entry["verified"] is True
    assert entry["cached"] is False
    assert entry["evidence"] == "jwt"
    assert entry["claimed_org_id"] == "o-1"
    # Privacy: never log raw user_id.
    assert entry["claimed_user_id_hash"] == hash_user_id("u-1")
    assert "u-1" not in str(entry).replace("u-1's hash placeholder", "")
    assert "reason" not in entry  # only present on deny


def test_emit_call_logs_at_warning_level_with_reason_on_deny() -> None:
    result = VerifyResult.deny("no_membership")

    with capture_logs() as captured:
        emit_call(
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            result=result,
            latency_ms=5.0,
        )

    assert len(captured) == 1
    entry = captured[0]
    assert entry["event"] == "identity_assert_call"
    assert entry["log_level"] == "warning"
    assert entry["verified"] is False
    assert entry["reason"] == "no_membership"
    assert "evidence" not in entry  # only present on allow
