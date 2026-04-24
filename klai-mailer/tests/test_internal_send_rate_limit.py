"""AC-3: per-recipient rate limit (10 sends / 24h).

Covers REQ-4.1 (ceiling + Retry-After), REQ-4.2 (sha256 hash keying),
REQ-4.3 (fail-open on Redis outage), REQ-4.4 (configurable), REQ-4.5
(failed validation doesn't deplete budget), REQ-4.6 (no cleartext email
in logs).
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from structlog.testing import capture_logs


def _load_main(redis_client):
    for mod in ("app.main", "app.config", "app.nonce", "app.rate_limit", "app.signature"):
        sys.modules.pop(mod, None)
    import app.nonce as nonce_mod
    import app.rate_limit as rl_mod
    nonce_mod.set_redis_client(redis_client)
    rl_mod.set_redis_client(redis_client)
    main = importlib.import_module("app.main")
    import app.nonce as n
    import app.rate_limit as r
    n.set_redis_client(redis_client)
    r.set_redis_client(redis_client)
    return main


@pytest.fixture
def client_fakeredis(settings_env, fake_redis, stub_smtp):
    main = _load_main(fake_redis)
    return TestClient(main.app)


@pytest.fixture
def client_brokenredis(settings_env, broken_redis, stub_smtp):
    main = _load_main(broken_redis)
    return TestClient(main.app)


BASE_PAYLOAD = {
    "template": "join_request_admin",
    "to": "admin@test.example",
    "locale": "nl",
    "variables": {
        "name": "Alice",
        "email": "alice@test.example",
        "org_id": 42,
    },
}


@pytest.fixture
def portal_ok():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/\d+/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "admin@test.example"})
        )
        yield mock


# ---------------------------------------------------------------------------
# AC-3 headline: 11th send returns 429
# ---------------------------------------------------------------------------


def test_eleventh_send_returns_429(client_fakeredis, stub_smtp, portal_ok):
    """REQ-4.1: 10 sends pass, 11th returns 429 + Retry-After."""
    for _ in range(10):
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json=BASE_PAYLOAD,
        )
        assert resp.status_code == 200, resp.text

    # 11th
    with capture_logs() as events:
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json=BASE_PAYLOAD,
        )
    assert resp.status_code == 429
    assert resp.json() == {"detail": "recipient rate limit exceeded"}
    retry_after = resp.headers.get("Retry-After")
    assert retry_after and int(retry_after) > 0

    assert len(stub_smtp.sent) == 10, "11th send must be blocked before SMTP dispatch"

    rl_events = [e for e in events if e.get("event") == "mailer_recipient_rate_limited"]
    assert rl_events
    # REQ-4.6: recipient cleartext MUST NOT appear in the log event
    for e in rl_events:
        assert "admin@test.example" not in str(e)
        assert "recipient_hash" in e


def test_different_recipients_have_separate_budgets(client_fakeredis, stub_smtp):
    """Budgets are per-recipient — another org's admin is unaffected."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/42/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "admin@test.example"})
        )
        mock.get(url__regex=r"^.+/internal/org/99/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "other@test.example"})
        )

        # Exhaust org 42's budget
        for _ in range(10):
            client_fakeredis.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json=BASE_PAYLOAD,
            )

        other_payload = {
            **BASE_PAYLOAD,
            "to": "other@test.example",
            "variables": {**BASE_PAYLOAD["variables"], "org_id": 99},
        }
        # org 99's first send should still succeed
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json=other_payload,
        )
        assert resp.status_code == 200


def test_case_insensitive_collision(client_fakeredis, stub_smtp):
    """REQ-4.2: Admin@.. and admin@.. share a budget (sha256 of lowercase)."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"^.+/internal/org/\d+/admin-email$").mock(
            return_value=httpx.Response(200, json={"admin_email": "Admin@test.example"})
        )
        for i in range(10):
            to_value = "Admin@test.example" if i % 2 == 0 else "admin@test.example"
            resp = client_fakeredis.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json={**BASE_PAYLOAD, "to": to_value},
            )
            assert resp.status_code == 200, f"iter {i}: {resp.text}"
        # 11th with the other casing collides with the same hash bucket
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json={**BASE_PAYLOAD, "to": "ADMIN@test.example"},
        )
        assert resp.status_code == 429


def test_redis_unavailable_fails_open(
    client_brokenredis, stub_smtp, portal_ok, broken_redis
):
    """REQ-4.3: Redis down → allow sends, log mailer_rate_limit_redis_unavailable."""
    with capture_logs() as events:
        # Go past the normal ceiling — must all succeed
        for _ in range(15):
            resp = client_brokenredis.post(
                "/internal/send",
                headers={"X-Internal-Secret": "internal-test-secret"},
                json=BASE_PAYLOAD,
            )
            assert resp.status_code == 200, resp.text

    unavail_events = [
        e for e in events if e.get("event") == "mailer_rate_limit_redis_unavailable"
    ]
    assert unavail_events, "expected fail-open warning log"


def test_failed_validation_does_not_deplete_budget(
    client_fakeredis, stub_smtp, portal_ok
):
    """REQ-4.5: schema-invalid requests MUST NOT count toward the budget."""
    # 10 invalid requests (missing org_id)
    bad_payload = {
        **BASE_PAYLOAD,
        "variables": {"name": "Alice", "email": "alice@test.example"},
    }
    for _ in range(10):
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json=bad_payload,
        )
        assert resp.status_code == 400

    # Now 10 valid requests — all should succeed (budget untouched)
    for _ in range(10):
        resp = client_fakeredis.post(
            "/internal/send",
            headers={"X-Internal-Secret": "internal-test-secret"},
            json=BASE_PAYLOAD,
        )
        assert resp.status_code == 200, resp.text
