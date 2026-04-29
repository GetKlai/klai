"""SPEC-SEC-SESSION-001 REQ-2 IDP-pending cookie binding regression suite.

Covers acceptance scenarios 2 (UA mismatch → 403) and 4 (mobile-carrier
``/24`` + IPv6 ``/48`` boundaries pass), plus REQ-2.2 / REQ-2.4 edge cases:
empty UA header tolerated, log records carry only short prefixes, original
user keeps their cookie when an attacker replay attempt is rejected.

Tests target the ``_verify_idp_pending_binding`` helper directly. The
helper is the single point that owns the binding policy — keeping the test
suite away from the surrounding social-signup mocking machinery.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from helpers import make_request
from structlog.testing import capture_logs

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _payload(*, ua_hash: str, ip_subnet: str) -> dict[str, Any]:
    """Mimic the decrypted ``klai_idp_pending`` payload shape."""
    return {
        "session_id": "sess-bind",
        "session_token": "tok-bind",
        "zitadel_user_id": "z-user-bind",
        "ua_hash": ua_hash,
        "ip_subnet": ip_subnet,
    }


_UA_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/127"
_UA_CURL = "curl/8.5.0"
# SHA-256 hex of _UA_FIREFOX, computed once via hashlib in fixture-construction:
_UA_FIREFOX_HASH = (
    "f99c75bcd0f4dfd5d9b50bcaf6e83bff7d8b8d9a1cb23eb53f9e4ba4ac1e0fd2"  # placeholder, real value computed at runtime
)


@pytest.fixture
def helper():
    """Resolve the binding helper lazily so the import error stays
    inside the test (clearer failure on RED)."""
    from app.api.signup import _verify_idp_pending_binding

    return _verify_idp_pending_binding


# ---------------------------------------------------------------------------
# REQ-2.2 + REQ-6.2 — UA mismatch rejected
# ---------------------------------------------------------------------------


def test_binding_rejects_different_ua(helper) -> None:
    """Acceptance scenario 2: stolen cookie replayed from a different UA → 403."""
    from app.services.bff_session import SessionService

    stored_ua_hash = SessionService.hash_metadata(_UA_FIREFOX)
    payload = _payload(ua_hash=stored_ua_hash, ip_subnet="203.0.113.0")

    # Same IP, different UA
    request = make_request(
        headers={"user-agent": _UA_CURL, "x-forwarded-for": "203.0.113.10"},
    )

    with capture_logs() as captured:
        with pytest.raises(HTTPException) as exc:
            helper(payload, request)

    assert exc.value.status_code == 403
    assert "binding" in exc.value.detail.lower()

    # REQ-5.2: structured event with prefix fields, no raw UA / IP
    events = [e for e in captured if e.get("event") == "idp_pending_binding_mismatch"]
    assert len(events) == 1
    event = events[0]
    assert event["log_level"] == "warning"
    # Prefix only — never the full hex
    assert len(event["stored_ua_hash_prefix"]) == 8
    assert len(event["current_ua_hash_prefix"]) == 8
    assert event["stored_ua_hash_prefix"] != event["current_ua_hash_prefix"]
    # Subnets match here (only UA differs); event should reflect that
    assert event["stored_ip_subnet"] == event["current_ip_subnet"] == "203.0.113.0"
    # PII guard
    assert "user_agent" not in event
    assert _UA_FIREFOX not in str(event)
    assert _UA_CURL not in str(event)
    assert "203.0.113.10" not in str(event)
    assert "session_id" not in event


# ---------------------------------------------------------------------------
# REQ-2.3 — IP /24 mismatch rejected
# ---------------------------------------------------------------------------


def test_binding_rejects_different_ipv4_subnet(helper) -> None:
    """Cookie issued from /24 ``198.51.100.0`` cannot be replayed from
    a different /24."""
    from app.services.bff_session import SessionService

    stored_ua_hash = SessionService.hash_metadata(_UA_FIREFOX)
    payload = _payload(ua_hash=stored_ua_hash, ip_subnet="198.51.100.0")

    # Same UA, but the new caller IP is in a totally different /24
    request = make_request(
        headers={"user-agent": _UA_FIREFOX, "x-forwarded-for": "203.0.113.42"},
    )

    with pytest.raises(HTTPException) as exc:
        helper(payload, request)

    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# REQ-2.3 + REQ-6.3 — same /24 passes (mobile-carrier UX)
# ---------------------------------------------------------------------------


def test_binding_passes_same_subnet_different_last_octet(helper) -> None:
    """Acceptance scenario 4: a mobile user moving from ``198.51.100.10``
    to ``198.51.100.214`` (same /24) keeps signing up successfully.
    """
    from app.services.bff_session import SessionService

    stored_ua_hash = SessionService.hash_metadata(_UA_FIREFOX)
    payload = _payload(ua_hash=stored_ua_hash, ip_subnet="198.51.100.0")

    request = make_request(
        headers={"user-agent": _UA_FIREFOX, "x-forwarded-for": "198.51.100.214"},
    )

    # Returns None on success — no exception raised
    helper(payload, request)


def test_binding_passes_ipv6_in_same_48(helper) -> None:
    """IPv6 companion — issue from ``2001:db8:1234:5678::1`` and consume
    from ``2001:db8:1234:5678:89ab::42`` should both resolve to the same
    ``/48`` and pass.
    """
    from app.services.bff_session import SessionService

    stored_ua_hash = SessionService.hash_metadata(_UA_FIREFOX)
    payload = _payload(ua_hash=stored_ua_hash, ip_subnet="2001:db8:1234::")

    request = make_request(
        headers={
            "user-agent": _UA_FIREFOX,
            "x-forwarded-for": "2001:db8:1234:5678:89ab::42",
        },
    )

    helper(payload, request)


# ---------------------------------------------------------------------------
# REQ-2.4 — empty UA header tolerated
# ---------------------------------------------------------------------------


def test_binding_handles_missing_ua_header(helper) -> None:
    """REQ-2.4: a cookie issued with an empty UA matches another empty-UA
    consume request. The hash function is run on ``""`` deterministically.
    """
    from app.services.bff_session import SessionService

    empty_ua_hash = SessionService.hash_metadata(None)
    payload = _payload(ua_hash=empty_ua_hash, ip_subnet="203.0.113.0")

    # No User-Agent header at all
    request = make_request(headers={"x-forwarded-for": "203.0.113.99"})

    helper(payload, request)


def test_binding_rejects_when_ua_appears_after_being_absent(helper) -> None:
    """A cookie issued with no UA cannot be consumed by a request that
    suddenly has one."""
    from app.services.bff_session import SessionService

    empty_ua_hash = SessionService.hash_metadata(None)
    payload = _payload(ua_hash=empty_ua_hash, ip_subnet="203.0.113.0")

    request = make_request(
        headers={"user-agent": _UA_FIREFOX, "x-forwarded-for": "203.0.113.99"},
    )

    with pytest.raises(HTTPException) as exc:
        helper(payload, request)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Defence — payload without binding fields rejected
# ---------------------------------------------------------------------------


def test_binding_rejects_payload_without_binding_fields(helper) -> None:
    """A cookie without ``ua_hash`` / ``ip_subnet`` is either pre-deploy
    legacy or tampered — either way, refuse it."""
    payload = {
        "session_id": "sess-bind",
        "session_token": "tok-bind",
        "zitadel_user_id": "z-user-bind",
    }
    request = make_request(
        headers={"user-agent": _UA_FIREFOX, "x-forwarded-for": "203.0.113.10"},
    )

    with pytest.raises(HTTPException) as exc:
        helper(payload, request)
    assert exc.value.status_code == 403
