"""AC-1 + unit: Jinja2 sandbox + StrictUndefined + Pydantic schema.

Covers REQ-1.1 (sandbox render), REQ-1.2 (dunder access blocked),
REQ-1.3 (StrictUndefined), REQ-2.1/2.3 (per-template schema rejects
unknown + invalid values), REQ-2.4 (branding injected from settings,
not caller).
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest
import respx
from fastapi.testclient import TestClient


def _load_main(settings_env, fake_redis, stub_smtp):
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
    main = _load_main(settings_env, fake_redis, stub_smtp)
    return TestClient(main.app)


@pytest.fixture
def respx_mock_portal(settings_env):
    """Mock portal-api admin-email lookup."""
    with respx.mock(assert_all_called=False) as router:
        router.get(
            url__regex=r"^.+/internal/org/\d+/admin-email$"
        ).mock(return_value=httpx.Response(200, json={"admin_email": "admin@test.example"}))
        yield router


# ---------------------------------------------------------------------------
# AC-1: str.format introspection payload is rejected
# ---------------------------------------------------------------------------


def test_str_format_payload_rejected(client, respx_mock_portal, stub_smtp):
    """REQ-1.1 + REQ-2.1: attacker string in variables is either rejected
    (schema/sandbox) or rendered literally — never executed as Python."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_admin",
            "to": "admin@test.example",
            "locale": "nl",
            "variables": {
                "name": "{__class__.__mro__[1].__subclasses__}",
                "email": "alice@test.example",
                "org_id": 42,
            },
        },
    )

    if resp.status_code == 200:
        # Literal-string render path: the attacker string appears LITERALLY
        # in the body as plain text. That is safe — the test's sole job is
        # to prove no Python-object graph content leaked into the email.
        assert len(stub_smtp.sent) == 1
        body_html = stub_smtp.sent[0]["html_body"]
        # No secrets, no class objects from actual introspection
        assert "smtp_password" not in body_html
        assert "webhook_secret" not in body_html
        assert "internal-test-secret" not in body_html
        assert "Settings object at" not in body_html
        assert "<class '" not in body_html
        assert "built-in method" not in body_html
    else:
        # Rejected path: 400 is the other acceptable outcome.
        assert resp.status_code == 400
        assert stub_smtp.sent == []


def test_unknown_keys_rejected(client, respx_mock_portal, stub_smtp):
    """REQ-2.1: `extra="forbid"` rejects unknown variables."""
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
                "injected_extra": "evil",  # unknown key
            },
        },
    )
    assert resp.status_code == 400
    assert stub_smtp.sent == []


def test_missing_required_key_returns_400(client, respx_mock_portal, stub_smtp):
    """REQ-2.3: missing `org_id` → ValidationError → 400."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_admin",
            "to": "admin@test.example",
            "locale": "nl",
            "variables": {"name": "Alice", "email": "alice@test.example"},
        },
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "invalid variables"}


def test_malformed_email_rejected(client, respx_mock_portal, stub_smtp):
    """REQ-2.1: EmailStr refuses non-email strings."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_admin",
            "to": "admin@test.example",
            "locale": "nl",
            "variables": {
                "name": "Alice",
                "email": "not-an-email",
                "org_id": 42,
            },
        },
    )
    assert resp.status_code == 400


def test_unknown_template_returns_400(client, respx_mock_portal, stub_smtp):
    """REQ-2.2: unknown template name is rejected before schema resolve."""
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "does_not_exist",
            "to": "admin@test.example",
            "locale": "nl",
            "variables": {},
        },
    )
    assert resp.status_code == 400
    assert "Unknown template" in resp.json()["detail"]


def test_branding_not_overridable_by_caller(
    client, respx_mock_portal, stub_smtp, settings_env
):
    """REQ-2.4: `brand_url` comes from settings even if caller tries to
    inject it via `variables`. The extra key is rejected outright by
    extra=forbid."""
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
                "brand_url": "https://phisher.evil",
            },
        },
    )
    # Rejected because `brand_url` is not in the schema (extra=forbid)
    assert resp.status_code == 400


def test_validation_failure_does_not_leak_long_attacker_strings(
    client, respx_mock_portal, stub_smtp
):
    """REQ-2.3: long attacker values are truncated in the error response."""
    long_value = "A" * 500
    resp = client.post(
        "/internal/send",
        headers={"X-Internal-Secret": "internal-test-secret"},
        json={
            "template": "join_request_admin",
            "to": "admin@test.example",
            "locale": "nl",
            "variables": {
                "name": "Alice",
                "email": long_value,  # clearly not EmailStr
                "org_id": 42,
            },
        },
    )
    assert resp.status_code == 400
    # The response body is the canonical "invalid variables" message.
    # Attacker value is NOT echoed.
    assert resp.json() == {"detail": "invalid variables"}
    assert long_value not in resp.text


def test_sandbox_blocks_dunder_via_template_context(client, respx_mock_portal):
    """REQ-1.2: even if the schema allowed a dotted attribute string,
    the sandbox would refuse to resolve it. Unit-level: verify the
    renderer itself blocks dunder access."""
    import importlib as _il
    import sys as _sys
    for mod in ("app.renderer", "app.config"):
        _sys.modules.pop(mod, None)
    renderer_mod = _il.import_module("app.renderer")
    from pathlib import Path
    r = renderer_mod.Renderer(
        theme_dir=Path("theme")
    )
    # Write a tiny template that tries dunder access — use Renderer's
    # env directly
    template = r._theme_env.from_string("{{ x.__class__ }}")
    from jinja2.exceptions import SecurityError
    with pytest.raises(SecurityError):
        template.render(x="test")
