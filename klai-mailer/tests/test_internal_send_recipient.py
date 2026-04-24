"""AC-2: recipient binding to template-derived expectation.

Covers REQ-3.1 (join_request_admin → portal-api admin-email),
REQ-3.2 (join_request_approved → variables.email), REQ-3.4 (fail-closed
on portal-api outage).
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from structlog.testing import capture_logs


def _load_main(fake_redis):
    for mod in ("app.main", "app.config", "app.nonce", "app.rate_limit", "app.signature"):
        sys.modules.pop(mod, None)
    import app.nonce as nonce_mod
    import app.rate_limit as rl_mod
    nonce_mod.set_redis_client(fake_redis)
    rl_mod.set_redis_client(fake_redis)
    main = importlib.import_module("app.main")
    import app.nonce as n
    import app.rate_limit as r
    n.set_redis_client(fake_redis)
    r.set_redis_client(fake_redis)
    return main


@pytest.fixture
def client(settings_env, fake_redis, stub_smtp):
    main = _load_main(fake_redis)
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# REQ-3.1: join_request_admin recipient binding
# ---------------------------------------------------------------------------


def test_recipient_mismatch_rejected(client, stub_smtp):
    """Caller-supplied `to` != portal-api admin_email → 400."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/42/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "admin@test.example"})
        )
        with capture_logs() as events:
            resp = client.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json={
                    "template": "join_request_admin",
                    "to": "attacker@evil.example",
                    "locale": "nl",
                    "variables": {
                        "name": "Alice",
                        "email": "alice@test.example",
                        "org_id": 42,
                    },
                },
            )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "recipient mismatch"}
    assert stub_smtp.sent == []

    mismatch_events = [e for e in events if e.get("event") == "mailer_recipient_mismatch"]
    assert mismatch_events, f"expected mailer_recipient_mismatch event; got {events}"
    # Privacy: cleartext email MUST NOT appear in logs
    for e in mismatch_events:
        assert "attacker@evil.example" not in str(e)
        assert "admin@test.example" not in str(e)


def test_recipient_match_accepted_uses_resolved_email(client, stub_smtp):
    """Correct match → email sent to the resolved admin email."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/42/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "admin@test.example"})
        )
        resp = client.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json={
                "template": "join_request_admin",
                "to": "admin@test.example",
                "locale": "nl",
                "variables": {
                    "name": "Alice",
                    "email": "alice@test.example",
                    "org_id": 42,
                },
            },
        )
    assert resp.status_code == 200, resp.text
    assert len(stub_smtp.sent) == 1
    assert stub_smtp.sent[0]["to_address"] == "admin@test.example"


def test_recipient_match_case_insensitive(client, stub_smtp):
    """REQ-3.1: `to` comparison is case-insensitive."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/\d+/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "Admin@Test.Example"})
        )
        resp = client.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json={
                "template": "join_request_admin",
                "to": "ADMIN@test.example",
                "locale": "nl",
                "variables": {
                    "name": "Alice",
                    "email": "alice@test.example",
                    "org_id": 42,
                },
            },
        )
    assert resp.status_code == 200, resp.text


def test_portal_api_unreachable_returns_503(client, stub_smtp):
    """REQ-3.4: fail-closed when portal-api is unreachable."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/\d+/admin-email$").mock(
            side_effect=httpx.ConnectError("nope")
        )
        with capture_logs() as events:
            resp = client.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json={
                    "template": "join_request_admin",
                    "to": "admin@test.example",
                    "locale": "nl",
                    "variables": {
                        "name": "Alice",
                        "email": "alice@test.example",
                        "org_id": 42,
                    },
                },
            )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "recipient lookup unavailable"}
    assert stub_smtp.sent == []
    lookup_events = [e for e in events if e.get("event") == "mailer_recipient_lookup_failed"]
    assert lookup_events


# ---------------------------------------------------------------------------
# REQ-3.2: join_request_approved recipient binding (variables.email)
# ---------------------------------------------------------------------------


def test_approved_recipient_from_variables_email(client, stub_smtp):
    """REQ-3.2: recipient is validated_vars.email."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_approved",
            "to": "bob@test.example",
            "locale": "nl",
            "variables": {
                "name": "Bob",
                "email": "bob@test.example",
                "workspace_url": "https://app.klai.example",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert stub_smtp.sent[0]["to_address"] == "bob@test.example"


def test_approved_to_mismatch_rejected(client, stub_smtp):
    """REQ-3.2: `to` != variables.email → 400 recipient mismatch."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_approved",
            "to": "attacker@evil.example",
            "locale": "nl",
            "variables": {
                "name": "Bob",
                "email": "bob@test.example",
                "workspace_url": "https://app.klai.example",
            },
        },
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "recipient mismatch"}
    assert stub_smtp.sent == []


def test_approved_missing_to_falls_back_to_email(client, stub_smtp):
    """REQ-3.2 alternative: empty `to` → use variables.email."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_approved",
            "to": "",
            "locale": "nl",
            "variables": {
                "name": "Bob",
                "email": "bob@test.example",
                "workspace_url": "https://app.klai.example",
            },
        },
    )
    assert resp.status_code == 200
    assert stub_smtp.sent[0]["to_address"] == "bob@test.example"
