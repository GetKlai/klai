"""Tests for SPEC-SEC-008 F-017 hardening of the Zitadel OIDC auth middleware.

Coverage:
- Constant-time portal-secret compare (hmac.compare_digest)
- Fail-closed behaviour when portal_caller_secret is empty
- Zitadel audience verification (string + list aud claims)
- Warn-only fallback when zitadel_api_audience is unset
- Bypass branch does not run audience check
- Static analysis: the module imports `hmac` and uses `hmac.compare_digest`

The LRU cache itself is covered by test_auth_middleware_cache.py (SPEC-SEC-007).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import auth as auth_module
from app.middleware.auth import AuthMiddleware, _audience_matches

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    portal_secret: str = "",
    audience: str = "",
) -> SimpleNamespace:
    """Build the minimal Settings-shape that AuthMiddleware.__init__ reads."""
    return SimpleNamespace(
        zitadel_introspection_url="https://example.test/oauth/v2/introspect",
        zitadel_client_id="cid",
        zitadel_client_secret="csecret",
        portal_caller_secret=portal_secret,
        zitadel_api_audience=audience,
    )


class _Recorder:
    """Class-level stub for AuthMiddleware._introspect.

    BaseHTTPMiddleware instantiates a fresh wrapper per request; patching an
    instance attribute does not reliably stick. Patch the class attribute
    instead. Because we assign a callable *instance* (not a function), Python's
    descriptor protocol does NOT bind it to the owner class — so ``__call__``
    only receives the ``token`` argument, not ``self``.
    """

    def __init__(self) -> None:
        self.calls: int = 0
        self.return_value: dict[str, Any] | None = None

    async def __call__(self, token: str) -> dict[str, Any] | None:  # noqa: ARG002
        self.calls += 1
        return self.return_value


def _build_app(
    settings: SimpleNamespace,
    introspect_return: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, _Recorder]:
    """Create a FastAPI app with a class-level stub of ``_introspect``."""
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, bool]:  # pragma: no cover - trivial route
        return {"ok": True}

    app.add_middleware(AuthMiddleware, settings=settings)

    recorder = _Recorder()
    recorder.return_value = introspect_return
    monkeypatch.setattr(AuthMiddleware, "_introspect", recorder)

    client = TestClient(app)
    return client, recorder


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Ensure test isolation — the module-level cache persists between tests."""
    auth_module._token_cache.clear()


# ---------------------------------------------------------------------------
# _audience_matches unit tests
# ---------------------------------------------------------------------------


class TestAudienceMatches:
    def test_string_match(self) -> None:
        assert _audience_matches("klai-connector", "klai-connector") is True

    def test_string_mismatch(self) -> None:
        assert _audience_matches("klai-scribe", "klai-connector") is False

    def test_list_contains(self) -> None:
        assert _audience_matches(["klai-connector", "klai-api"], "klai-connector") is True

    def test_list_does_not_contain(self) -> None:
        assert _audience_matches(["klai-scribe", "klai-api"], "klai-connector") is False

    def test_none_claim(self) -> None:
        assert _audience_matches(None, "klai-connector") is False

    def test_non_string_non_list_claim(self) -> None:
        # Defensive: a malformed aud (int, dict) never matches.
        assert _audience_matches(42, "klai-connector") is False
        assert _audience_matches({"x": 1}, "klai-connector") is False


# ---------------------------------------------------------------------------
# Static / source-level assertions
# ---------------------------------------------------------------------------


class TestModuleSource:
    def test_hmac_imported(self) -> None:
        """The middleware module imports the stdlib `hmac` module."""
        assert hasattr(auth_module, "hmac")

    def test_hmac_compare_digest_used(self) -> None:
        """The source uses hmac.compare_digest (not == / !=) on portal_secret."""
        source_path = Path(inspect.getfile(auth_module))
        src = source_path.read_text(encoding="utf-8")
        assert "hmac.compare_digest" in src, "portal_caller_secret compare must use hmac.compare_digest"
        # Defense: nobody introduced a raw `token == self._portal_secret` back in.
        assert "token == self._portal_secret" not in src


# ---------------------------------------------------------------------------
# Portal-secret bypass path
# ---------------------------------------------------------------------------


class TestPortalBypass:
    def test_matching_portal_secret_bypasses_introspection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When portal_secret is set and the bearer token matches, no introspection runs."""
        settings = _make_settings(portal_secret="portal-shared-secret")
        client, recorder = _build_app(
            settings,
            introspect_return={"active": True, "aud": "klai-connector"},
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer portal-shared-secret"})
        assert resp.status_code == 200
        # Introspection MUST NOT be called on the bypass path.
        assert recorder.calls == 0

    def test_nonmatching_portal_secret_falls_through_to_introspection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A different bearer value triggers introspection rather than bypass."""
        settings = _make_settings(portal_secret="portal-shared-secret")
        client, recorder = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": "klai-connector",
                "urn:zitadel:iam:user:resourceowner:id": "org-123",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer some-other-token"})
        assert resp.status_code == 200
        assert recorder.calls == 1

    def test_empty_portal_secret_never_bypasses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: when portal_secret is '', the bypass branch never runs.

        Even if hmac.compare_digest were given two empty bytestrings (which it
        would consider equal), the `self._portal_secret` null-check short-circuits
        first. We test by presenting a random token and asserting that the
        request goes to introspection, not through the bypass.
        """
        settings = _make_settings(portal_secret="")
        # introspect_return is a valid claim, so a successful introspection call
        # is the signal that the bypass did NOT fire.
        client, recorder = _build_app(
            settings,
            introspect_return={
                "active": True,
                "urn:zitadel:iam:user:resourceowner:id": "org-x",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer anything"})
        assert resp.status_code == 200
        assert recorder.calls == 1, "empty portal_secret must not short-circuit to bypass"


# ---------------------------------------------------------------------------
# Audience verification path
# ---------------------------------------------------------------------------


class TestAudienceVerification:
    def test_correct_audience_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": "klai-connector",
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer good-token"})
        assert resp.status_code == 200

    def test_wrong_audience_string_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": "klai-scribe",
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer wrong-aud"})
        assert resp.status_code == 401

    def test_wrong_audience_list_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": ["klai-scribe", "klai-api"],
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer wrong-list"})
        assert resp.status_code == 401

    def test_audience_in_list_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": ["klai-scribe", "klai-connector"],
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer ok-list"})
        assert resp.status_code == 200

    def test_missing_audience_claim_rejected_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                # no `aud` field at all
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer no-aud"})
        assert resp.status_code == 401

    def test_wrong_audience_is_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rejection path must not pollute the positive-cache."""
        settings = _make_settings(audience="klai-connector")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": "klai-scribe",
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401
        assert len(auth_module._token_cache) == 0


class TestAudienceUnconfiguredFallback:
    def test_no_audience_configured_skips_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ZITADEL_API_AUDIENCE is empty, claims without aud are still accepted."""
        settings = _make_settings(audience="")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                # no aud at all — should still pass because audience is not configured
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer warn-only"})
        assert resp.status_code == 200

    def test_no_audience_configured_accepts_any_aud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warn-only fallback: any aud value is accepted when unconfigured."""
        settings = _make_settings(audience="")
        client, _ = _build_app(
            settings,
            introspect_return={
                "active": True,
                "aud": "something-else-entirely",
                "urn:zitadel:iam:user:resourceowner:id": "org-xyz",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/ping", headers={"Authorization": "Bearer any-aud"})
        assert resp.status_code == 200

    def test_startup_warning_when_audience_empty(self, caplog: pytest.LogCaptureFixture) -> None:
        """Constructor logs a warning so operators notice the missing audience."""
        import logging

        caplog.set_level(logging.WARNING, logger="app.middleware.auth")
        settings = _make_settings(audience="")
        # Instantiate directly — we just want the constructor log.
        AuthMiddleware(app=MagicMock(), settings=settings)  # type: ignore[arg-type]
        assert any("zitadel_api_audience is empty" in rec.getMessage() for rec in caplog.records), (
            "Expected a warn-only notice when audience is unconfigured"
        )


# ---------------------------------------------------------------------------
# Auth-header handling (regression coverage)
# ---------------------------------------------------------------------------


class TestAuthHeader:
    def test_missing_authorization_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings()
        client, _ = _build_app(settings, introspect_return=None, monkeypatch=monkeypatch)
        resp = client.get("/ping")
        assert resp.status_code == 401

    def test_non_bearer_authorization_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _make_settings()
        client, _ = _build_app(settings, introspect_return=None, monkeypatch=monkeypatch)
        resp = client.get("/ping", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 401

    def test_health_endpoint_skips_auth(self) -> None:
        settings = _make_settings()
        app = FastAPI()

        @app.get("/health")
        async def health() -> dict[str, str]:  # pragma: no cover - trivial route
            return {"status": "ok"}

        app.add_middleware(AuthMiddleware, settings=settings)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
