"""AC-6: Uniform 401 body for every signature-verification failure.

Covers REQ-7.1 (identical response body), REQ-7.2 (distinct log reason),
REQ-7.3 (no side-channel headers), REQ-10.1 (unknown vN fields rejected).

Every failure mode MUST produce:
- HTTP 401
- body EXACTLY == {"detail": "invalid signature"} (byte-identical across modes)
- no WWW-Authenticate (or other phase-revealing) header
- a structured log event with a distinct `reason` sub-field.
"""

from __future__ import annotations

import importlib
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests._signing import sign, sign_with_extra

VALID_BODY = b'{"contextInfo":{"eventType":"x"},"templateData":{"text":"hi"}}'


@pytest.fixture
def client(settings_env, stub_smtp):
    for mod in ("app.main", "app.config", "app.signature"):
        sys.modules.pop(mod, None)
    main = importlib.import_module("app.main")
    return TestClient(main.app)


@pytest.fixture
def captured_logs():
    """Capture structlog events emitted during a test via capture_logs()."""
    with capture_logs() as events:
        yield events


# ---------------------------------------------------------------------------
# AC-6 headline: byte-identical 401 body across every failure mode
# ---------------------------------------------------------------------------


@pytest.fixture
def failure_cases(settings_env):
    """Build the 5 failure modes from AC-6's table."""
    secret = settings_env["WEBHOOK_SECRET"]
    _, valid_ts = sign(VALID_BODY, secret)
    old_header, _ = sign(VALID_BODY, secret, timestamp=valid_ts - 400)

    return [
        ("missing_header", {}),
        ("malformed_header", {"ZITADEL-Signature": "garbage"}),
        ("timestamp_out_of_window", {"ZITADEL-Signature": old_header}),
        ("hmac_mismatch", {"ZITADEL-Signature": f"t={int(time.time())},v1=deadbeef"}),
        (
            "unknown_vN_field",
            {"ZITADEL-Signature": sign_with_extra(VALID_BODY, secret, "v2=x")},
        ),
    ]


def test_uniform_401_body_across_failure_modes(client, failure_cases):
    """REQ-7.1: response body byte-identical for every failure."""
    bodies = []
    for _, headers in failure_cases:
        resp = client.post("/notify", content=VALID_BODY, headers=headers)
        assert resp.status_code == 401, f"expected 401, got {resp.status_code} for {headers}"
        bodies.append(resp.content)

    first = bodies[0]
    for i, b in enumerate(bodies[1:], start=1):
        assert b == first, (
            f"body-{i} differs from body-0: {b!r} vs {first!r}"
        )
    assert json.loads(first) == {"detail": "invalid signature"}


def test_no_side_channel_headers(client, failure_cases):
    """REQ-7.3: no WWW-Authenticate / phase-revealing response header."""
    forbidden = {"www-authenticate", "x-signature-phase", "x-auth-error-kind"}
    for _, headers in failure_cases:
        resp = client.post("/notify", content=VALID_BODY, headers=headers)
        lowered = {k.lower() for k in resp.headers}
        leaked = forbidden & lowered
        assert not leaked, f"forbidden response headers: {leaked}"


def test_log_reason_distinct_per_mode(client, failure_cases, captured_logs):
    """REQ-7.2: each failure emits a distinct log `reason`."""
    reasons_seen: list[str] = []
    for expected_reason, headers in failure_cases:
        captured_logs.clear()
        resp = client.post("/notify", content=VALID_BODY, headers=headers)
        assert resp.status_code == 401
        sig_events = [
            e for e in captured_logs
            if e.get("event") == "mailer_signature_invalid"
        ]
        assert sig_events, (
            f"no mailer_signature_invalid event for {expected_reason}; "
            f"captured: {captured_logs}"
        )
        reasons = [e.get("reason") for e in sig_events]
        assert expected_reason in reasons, (
            f"expected reason={expected_reason!r} for headers={headers}, "
            f"got reasons={reasons}"
        )
        reasons_seen.append(expected_reason)

    # All 5 distinct
    assert len(set(reasons_seen)) == 5


# ---------------------------------------------------------------------------
# AC-8 headline: extra v2 field is rejected
# ---------------------------------------------------------------------------


def test_extra_v2_field_rejected(client, settings_env, stub_smtp, captured_logs):
    """REQ-10.1: unknown vN field → 401 + reason=unknown_vN_field."""
    header = sign_with_extra(VALID_BODY, settings_env["WEBHOOK_SECRET"], "v2=unexpected")
    resp = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid signature"}
    assert stub_smtp.sent == []

    sig_events = [e for e in captured_logs if e.get("event") == "mailer_signature_invalid"]
    assert any(e.get("reason") == "unknown_vN_field" for e in sig_events)
    # The unknown field MUST be enumerated for operator visibility
    assert any("v2" in (e.get("unknown_fields") or []) for e in sig_events)


def test_six_token_header_rejected(client, settings_env):
    """REQ-10.3: more than 5 tokens → reject."""
    base, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    header = base + ",a=1,b=2,c=3,d=4"  # 5 extra → total 6 tokens
    resp = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    assert resp.status_code == 401


def test_non_v_prefixed_unknown_key_rejected(client, settings_env):
    """REQ-10.1: any key outside {t, v1} is rejected — not only vN siblings."""
    header = sign_with_extra(VALID_BODY, settings_env["WEBHOOK_SECRET"], "ver=1")
    resp = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    assert resp.status_code == 401


def test_valid_signature_succeeds(client, settings_env, stub_smtp):
    """Regression: a well-formed header (t + v1 only) still works."""
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])
    resp = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    # May be 422 (payload validation) or 200 (full render); both prove
    # signature passed. Critically NOT 401.
    assert resp.status_code != 401, resp.text
