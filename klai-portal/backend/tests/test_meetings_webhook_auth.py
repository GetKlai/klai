"""
SPEC-SEC-F033: Vexa webhook auth — fail-closed + constant-time compare.

Covers:
- startup fails when VEXA_WEBHOOK_SECRET is empty (pydantic model_validator)
- _require_webhook_secret uses hmac.compare_digest for the Bearer comparison
- 401 on wrong Bearer
- 200 (pass) on correct Bearer
- Docker-network IP is still trusted without a Bearer
"""

from __future__ import annotations

import hmac
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.meetings import _require_webhook_secret
from app.core.config import Settings


def _make_request(client_host: str | None, auth_header: str | None) -> MagicMock:
    """Build a minimal Request-like mock for the dependency helper."""
    req = MagicMock()
    req.client = MagicMock(host=client_host) if client_host is not None else None
    req.headers = {}
    if auth_header is not None:
        req.headers = {"Authorization": auth_header}
    return req


# ---------------------------------------------------------------------------
# Startup-fail test: empty VEXA_WEBHOOK_SECRET -> ValidationError
# ---------------------------------------------------------------------------


def test_settings_startup_fails_without_vexa_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construct Settings with an empty VEXA_WEBHOOK_SECRET and confirm it aborts.

    The conftest already sets the env var at module-import time; we override it
    explicitly inside this test. Settings() is constructed fresh so the
    @model_validator runs against the overridden env.
    """
    # Ensure the other required fields stay valid so we isolate the vexa check.
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("ZITADEL_PAT", os.environ["ZITADEL_PAT"])
    monkeypatch.setenv("SSO_COOKIE_KEY", os.environ["SSO_COOKIE_KEY"])
    monkeypatch.setenv("PORTAL_SECRETS_KEY", os.environ["PORTAL_SECRETS_KEY"])
    monkeypatch.setenv("ENCRYPTION_KEY", os.environ["ENCRYPTION_KEY"])
    monkeypatch.setenv("VEXA_WEBHOOK_SECRET", "")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "VEXA_WEBHOOK_SECRET" in str(excinfo.value)


def test_settings_startup_fails_with_whitespace_only_vexa_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only value is also rejected (mirrors SEC-011 behaviour)."""
    monkeypatch.setenv("VEXA_WEBHOOK_SECRET", "   ")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "VEXA_WEBHOOK_SECRET" in str(excinfo.value)


# ---------------------------------------------------------------------------
# _require_webhook_secret behaviour
# ---------------------------------------------------------------------------


def test_require_webhook_secret_rejects_wrong_bearer() -> None:
    """External caller with the wrong Bearer gets 401."""
    # External IP (non-Docker-range) + wrong token.
    req = _make_request(client_host="203.0.113.5", auth_header="Bearer wrong-token")

    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as excinfo:
            _require_webhook_secret(req)

    assert excinfo.value.status_code == 401


def test_require_webhook_secret_accepts_correct_bearer() -> None:
    """External caller with the correct Bearer passes."""
    req = _make_request(
        client_host="203.0.113.5",
        auth_header="Bearer correct-secret",
    )

    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        # No exception means pass.
        _require_webhook_secret(req)


def test_require_webhook_secret_missing_authorization_header_rejects() -> None:
    """External caller without any Authorization header is rejected."""
    req = _make_request(client_host="203.0.113.5", auth_header=None)

    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as excinfo:
            _require_webhook_secret(req)

    assert excinfo.value.status_code == 401


def test_require_webhook_secret_docker_network_trusted_without_bearer() -> None:
    """Callers on the internal Docker networks (172.x/10.x/192.168.x) are trusted.

    This preserves pre-existing behaviour: meeting-api reaches portal-api via
    klai-net and must not be forced to embed the secret in POST_MEETING_HOOKS.
    Further hardening tracked in SEC-013.
    """
    for host in ("172.18.0.5", "10.0.0.2", "192.168.1.10"):
        req = _make_request(client_host=host, auth_header=None)
        with patch("app.api.meetings.settings") as mock_settings:
            mock_settings.vexa_webhook_secret = "correct-secret"
            _require_webhook_secret(req)  # no exception


def test_require_webhook_secret_uses_constant_time_compare() -> None:
    """Comparison MUST use hmac.compare_digest (constant-time).

    We spy on hmac.compare_digest as imported in the meetings module and assert
    it was invoked with byte-encoded header + expected-bearer arguments.
    """
    req = _make_request(client_host="203.0.113.5", auth_header="Bearer correct-secret")

    with (
        patch("app.api.meetings.settings") as mock_settings,
        patch("app.api.meetings.hmac.compare_digest", wraps=hmac.compare_digest) as spy,
    ):
        mock_settings.vexa_webhook_secret = "correct-secret"
        _require_webhook_secret(req)

    assert spy.called
    args, _kwargs = spy.call_args
    # Both operands are bytes (required by compare_digest for timing safety).
    assert isinstance(args[0], bytes)
    assert isinstance(args[1], bytes)
    assert args[0] == b"Bearer correct-secret"
    assert args[1] == b"Bearer correct-secret"
