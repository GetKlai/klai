"""AC-10: /internal/send uses hmac.compare_digest for X-Internal-Secret.

REQ-8.1: Replaces `!=` with hmac.compare_digest in the auth helper.
REQ-8.2: The check is factored into a helper (_validate_incoming_secret)
         so future routes cannot regress to `!=`.

The constant-time property is asserted by inspecting that the helper
contains an `hmac.compare_digest` call (textually), and the functional
behaviour is asserted via HTTP 401 for a range of wrong secrets.
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient


def _get_client(settings_env, stub_smtp):
    # Fresh import so REQ-9 validator and REQ-8 compare_digest land in the module
    import importlib
    import sys
    for mod in ("app.main", "app.config"):
        sys.modules.pop(mod, None)
    main = importlib.import_module("app.main")
    return TestClient(main.app), main


def _send(client, secret_header: str | None = None, body=None):
    headers = {}
    if secret_header is not None:
        headers["X-Internal-Secret"] = secret_header
    payload = body or {
        "template": "join_request_admin",
        "to": "admin@test.local",
        "locale": "nl",
        "variables": {"name": "Alice", "email": "a@test.local"},
    }
    return client.post("/internal/send", json=payload, headers=headers)


def test_correct_secret_is_accepted(settings_env, stub_smtp):
    """Sanity: the correct secret passes the auth gate."""
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header="internal-test-secret")
    # May still fail downstream once REQ-1..4 land; for REQ-8 we only check auth
    # returns a non-401 status.
    assert resp.status_code != 401, f"got {resp.status_code} with correct secret: {resp.text}"


def test_wrong_secret_same_length_returns_401(settings_env, stub_smtp):
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header="x" * len("internal-test-secret"))
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


def test_wrong_secret_short_returns_401(settings_env, stub_smtp):
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header="x")
    assert resp.status_code == 401


def test_wrong_secret_long_returns_401(settings_env, stub_smtp):
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header="y" * 128)
    assert resp.status_code == 401


def test_empty_secret_returns_401(settings_env, stub_smtp):
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header="")
    assert resp.status_code == 401


def test_missing_secret_header_returns_401(settings_env, stub_smtp):
    client, _ = _get_client(settings_env, stub_smtp)
    resp = _send(client, secret_header=None)
    assert resp.status_code == 401


def test_auth_helper_uses_compare_digest(settings_env, stub_smtp):
    """REQ-8.2: the factored helper calls hmac.compare_digest.

    Sources are inspected to ensure the `!=` antipattern is not reintroduced.
    """
    _, main = _get_client(settings_env, stub_smtp)
    helper = getattr(main, "_validate_incoming_secret", None)
    assert helper is not None, "expected _validate_incoming_secret helper"
    source = inspect.getsource(helper)
    assert "hmac.compare_digest" in source, (
        f"_validate_incoming_secret must use hmac.compare_digest; got:\n{source}"
    )
    assert "!=" not in source.split("\n", 1)[1], (
        "`!=` comparison against the shared secret is forbidden — use compare_digest"
    )


def test_no_rawless_comparison_on_internal_secret(settings_env, stub_smtp):
    """REQ-8.3: the `!= settings.internal_secret` antipattern is removed from main.py."""
    _, main = _get_client(settings_env, stub_smtp)
    source = inspect.getsource(main)
    # The bug pattern: direct `!=` comparison on the settings attribute
    assert "!= settings.internal_secret" not in source, (
        "main.py still contains the `!=` compare on internal_secret — use hmac.compare_digest"
    )
