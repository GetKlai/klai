"""
SPEC-SEC-WEBHOOK-001 REQ-2 — Vexa webhook auth hardening.

Supersedes the SEC-013 F-033 test suite. The IP-range bypass
(172.x / 10.x / 192.168.x short-circuit) is REMOVED — every caller,
including Docker-internal peers, MUST present a valid Bearer token.

Covers:
- startup fails when VEXA_WEBHOOK_SECRET is empty (pydantic model_validator, unchanged)
- _require_webhook_secret uses hmac.compare_digest for the Bearer comparison
- 401 on wrong Bearer
- 401 on Docker-network source IP with no Bearer (inverted legacy case)
- Pass on correct Bearer regardless of source IP
"""

from __future__ import annotations

import base64
import hmac
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.meetings import _require_webhook_secret
from app.core.config import Settings


def _basic_auth_header(user: str, password: str) -> str:
    """Build the `Authorization: Basic <base64>` header httpx would produce for
    a URL with userinfo (`http://user:password@host/path`)."""
    raw = f"{user}:{password}".encode()
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


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


def test_require_webhook_secret_docker_network_IP_no_bearer_rejects_401() -> None:
    """SPEC-SEC-WEBHOOK-001 REQ-2.2: source IP alone NEVER authenticates.

    Inverted from the legacy `test_require_webhook_secret_docker_network_trusted_without_bearer`
    test. Docker-internal source IPs (172.x / 10.x / 192.168.x) previously short-
    circuited the auth check — that was an auth bypass because Caddy's container
    IP always sat in those ranges for every external request. The IP-range
    early-return is deleted in REQ-2.1; every caller now MUST present a valid
    Bearer, full stop.
    """
    for host in ("172.18.0.5", "10.0.0.2", "192.168.1.10"):
        req = _make_request(client_host=host, auth_header=None)
        with patch("app.api.meetings.settings") as mock_settings:
            mock_settings.vexa_webhook_secret = "correct-secret"
            with pytest.raises(HTTPException) as excinfo:
                _require_webhook_secret(req)
            assert excinfo.value.status_code == 401, f"Docker-internal host {host} was accepted without a Bearer"


def test_require_webhook_secret_docker_network_IP_with_valid_bearer_passes() -> None:
    """Legitimate callers on klai-net (Vexa api-gateway) continue to work provided
    they present the Bearer — this covers the forcing function documented in the
    SPEC Assumptions: POST_MEETING_HOOKS must be (re)configured with
    `Authorization: Bearer <secret>` for the Vexa webhook flow to keep working.
    """
    for host in ("172.18.0.5", "10.0.0.2", "192.168.1.10"):
        req = _make_request(client_host=host, auth_header="Bearer correct-secret")
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


# ---------------------------------------------------------------------------
# Basic-auth branch (SPEC Assumption "URL variant that embeds the secret")
#
# Vexa's POST_MEETING_HOOKS currently cannot inject a Bearer header — but httpx
# converts URL userinfo into `Authorization: Basic <b64(user:password)>`. The
# guard accepts Basic as an alternative form; the password half MUST match
# settings.vexa_webhook_secret.
# ---------------------------------------------------------------------------


def test_require_webhook_secret_accepts_basic_auth_with_correct_password() -> None:
    """URL userinfo => httpx sends `Authorization: Basic <b64(user:secret)>`.

    The user component is ignored; any non-empty user is accepted. This mirrors
    what Vexa produces with `POST_MEETING_HOOKS=http://vexa:<secret>@portal-api/...`.
    """
    req = _make_request(
        client_host="172.18.0.5",  # Docker-internal, like real Vexa callback
        auth_header=_basic_auth_header("vexa", "correct-secret"),
    )
    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        _require_webhook_secret(req)  # no exception


def test_require_webhook_secret_rejects_basic_auth_with_wrong_password() -> None:
    """Wrong password in Basic header is rejected — matches the Bearer path."""
    req = _make_request(
        client_host="172.18.0.5",
        auth_header=_basic_auth_header("vexa", "wrong-secret"),
    )
    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as excinfo:
            _require_webhook_secret(req)
    assert excinfo.value.status_code == 401


def test_require_webhook_secret_basic_auth_ignores_user_component() -> None:
    """User component of Basic auth MUST NOT influence the decision. The guard
    only reads the password half after the first colon. Per RFC 7617 the user
    component cannot contain a colon, so `partition(":")` splits unambiguously.
    """
    for user in ("vexa", "", "any-random-user"):
        req = _make_request(
            client_host="172.18.0.5",
            auth_header=_basic_auth_header(user, "correct-secret"),
        )
        with patch("app.api.meetings.settings") as mock_settings:
            mock_settings.vexa_webhook_secret = "correct-secret"
            _require_webhook_secret(req)  # no exception


def test_require_webhook_secret_rejects_malformed_basic_auth() -> None:
    """Non-base64 payload in Basic header → 401, not 500. Prevents DoS via
    malformed auth."""
    req = _make_request(client_host="203.0.113.5", auth_header="Basic !!!not-valid-base64!!!")
    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as excinfo:
            _require_webhook_secret(req)
    assert excinfo.value.status_code == 401


def test_require_webhook_secret_rejects_basic_auth_without_colon() -> None:
    """Decoded Basic payload missing a colon is invalid per RFC 7617 → 401."""
    # `notacolon` base64 → "bm90YWNvbG9u"
    payload = base64.b64encode(b"notacolon").decode("ascii")
    req = _make_request(client_host="203.0.113.5", auth_header=f"Basic {payload}")
    with patch("app.api.meetings.settings") as mock_settings:
        mock_settings.vexa_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as excinfo:
            _require_webhook_secret(req)
    assert excinfo.value.status_code == 401


def test_require_webhook_secret_rejects_unknown_auth_scheme() -> None:
    """Digest, JWT-in-Authorization, etc. are not accepted — 401."""
    for header in ("Digest stuff", "JWT abc.def.ghi", "Token foo", "correct-secret"):
        req = _make_request(client_host="203.0.113.5", auth_header=header)
        with patch("app.api.meetings.settings") as mock_settings:
            mock_settings.vexa_webhook_secret = "correct-secret"
            with pytest.raises(HTTPException) as excinfo:
                _require_webhook_secret(req)
        assert excinfo.value.status_code == 401, f"scheme {header!r} was unexpectedly accepted"
