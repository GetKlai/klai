"""SPEC-SEC-AUDIT-2026-04 B2 acceptance tests: klai-connector audience validator.

Covers:
- test_settings_startup_fails_without_audience: pydantic validator raises on
  empty / whitespace-only KLAI_CONNECTOR_ZITADEL_AUDIENCE.
- test_audience_mismatch_rejected: JWT with wrong `aud` claim returns 401.
- test_audience_match_accepted: JWT with correct `aud` claim returns 200.

Background: before this fix the connector had a warn-only fallback that
silently skipped audience verification when KLAI_CONNECTOR_ZITADEL_AUDIENCE
was empty (SPEC-SEC-008 F-017 deviation). This allowed cross-app token reuse:
a JWT issued for any klai Zitadel app would pass connector auth as long as
Zitadel introspection returned active=true. This is the same bug class as
SPEC-SEC-012 (research-api) -- the connector was out of scope for the original
audit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app.middleware.auth import AuthMiddleware

# ---------------------------------------------------------------------------
# Shared valid settings kwargs (mirrors test_sec_internal_001.py pattern)
# The github_app_private_key value here is a deliberately invalid dummy — it
# passes the base64 decode branch gracefully and is never used for signing.
# ---------------------------------------------------------------------------

_DUMMY_GITHUB_KEY = "dGVzdC1rZXktbm90LXJlYWw="  # base64("test-key-not-real")

_VALID_SETTINGS_KWARGS: dict[str, str] = {
    # Required pydantic-settings fields with no default.
    "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
    "zitadel_introspection_url": "http://zitadel/oauth/v2/introspect",
    "zitadel_client_id": "test-client",
    "zitadel_client_secret": "test-zitadel-secret-12345",
    "github_app_id": "12345",
    "github_app_private_key": _DUMMY_GITHUB_KEY,
    "encryption_key": "0" * 64,
    "knowledge_ingest_url": "http://knowledge-ingest:8000",
    "knowledge_ingest_secret": "test-ingest-secret-12345",
    "portal_internal_secret": "test-portal-secret-12345",
    # SPEC-SEC-AUDIT-2026-04 B2: audience is mandatory.
    "zitadel_api_audience": "klai-connector-test-audience",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncIntrospectStub:
    """Class-level stub for AuthMiddleware._introspect.

    Patched on the class (not instance) so BaseHTTPMiddleware wrappers see it
    on every request. Descriptor protocol does not bind callable instances, so
    __call__ only receives `token`, not `self`.
    """

    def __init__(self, return_value: dict[str, Any] | None) -> None:
        self._rv = return_value

    async def __call__(self, token: str) -> dict[str, Any] | None:  # noqa: ARG002
        return self._rv


def _build_app(
    audience: str,
    introspect_return: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Return a TestClient with AuthMiddleware configured for the given audience."""
    settings = SimpleNamespace(
        zitadel_introspection_url="https://example.test/oauth/v2/introspect",
        zitadel_client_id="cid",
        zitadel_client_secret="csecret",
        portal_caller_secret="",
        zitadel_api_audience=audience,
    )
    app = FastAPI()

    @app.get("/resource")
    async def resource() -> dict[str, bool]:  # pragma: no cover - trivial route
        return {"ok": True}

    app.add_middleware(AuthMiddleware, settings=settings)
    monkeypatch.setattr(AuthMiddleware, "_introspect", _AsyncIntrospectStub(introspect_return))
    return TestClient(app)


# ---------------------------------------------------------------------------
# B2-1: Settings validator rejects empty / whitespace-only audience
# ---------------------------------------------------------------------------


class TestSettingsStartupFailsWithoutAudience:
    """SPEC-SEC-AUDIT-2026-04 B2: pydantic validator must reject empty audience.

    This mirrors the SPEC-SEC-012 validator in research-api. The validator
    ensures the service refuses to start rather than serving an insecure
    default. See also validator-env-parity pitfall in
    .claude/rules/klai/pitfalls/process-rules.md.
    """

    def test_empty_audience_raises_validation_error(self) -> None:
        from app.core.config import Settings

        with pytest.raises(ValidationError) as exc:
            Settings(**{**_VALID_SETTINGS_KWARGS, "zitadel_api_audience": ""})  # type: ignore[arg-type]
        assert "KLAI_CONNECTOR_ZITADEL_AUDIENCE" in str(exc.value)

    def test_whitespace_only_audience_raises_validation_error(self) -> None:
        from app.core.config import Settings

        with pytest.raises(ValidationError) as exc:
            Settings(**{**_VALID_SETTINGS_KWARGS, "zitadel_api_audience": "   "})  # type: ignore[arg-type]
        assert "KLAI_CONNECTOR_ZITADEL_AUDIENCE" in str(exc.value)

    def test_valid_audience_passes_validation(self) -> None:
        from app.core.config import Settings

        # Should not raise.
        s = Settings(**_VALID_SETTINGS_KWARGS)  # type: ignore[arg-type]
        assert s.zitadel_api_audience == "klai-connector-test-audience"


# ---------------------------------------------------------------------------
# B2-2: Middleware rejects tokens with wrong audience (cross-app reuse scenario)
# ---------------------------------------------------------------------------


class TestAudienceMismatchRejected:
    """Tokens issued for a different Zitadel app must return 401.

    This is the core B2 cross-app token reuse scenario: a JWT issued for
    klai-scribe (or any other service) passes Zitadel introspection (active=true)
    but is rejected by the audience check.
    """

    def test_wrong_audience_string_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(
            audience="klai-connector",
            introspect_return={
                "active": True,
                "aud": "klai-scribe",  # wrong app — cross-app token reuse attempt
                "urn:zitadel:iam:user:resourceowner:id": "org-123",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/resource", headers={"Authorization": "Bearer cross-app-token"})
        assert resp.status_code == 401

    def test_wrong_audience_list_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(
            audience="klai-connector",
            introspect_return={
                "active": True,
                "aud": ["klai-scribe", "klai-mailer"],  # connector not in list
                "urn:zitadel:iam:user:resourceowner:id": "org-123",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/resource", headers={"Authorization": "Bearer wrong-list-token"})
        assert resp.status_code == 401

    def test_missing_aud_claim_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tokens without any aud claim are rejected (audience always enforced)."""
        client = _build_app(
            audience="klai-connector",
            introspect_return={
                "active": True,
                # no aud claim at all
                "urn:zitadel:iam:user:resourceowner:id": "org-123",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/resource", headers={"Authorization": "Bearer no-aud-token"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# B2-3: Happy path -- correct audience is accepted
# ---------------------------------------------------------------------------


class TestAudienceMatchAccepted:
    """Tokens with the correct audience claim pass through normally."""

    def test_correct_audience_string_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(
            audience="klai-connector",
            introspect_return={
                "active": True,
                "aud": "klai-connector",
                "urn:zitadel:iam:user:resourceowner:id": "org-abc",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/resource", headers={"Authorization": "Bearer valid-token"})
        assert resp.status_code == 200

    def test_correct_audience_in_list_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zitadel may return aud as a list; connector must be present to accept."""
        client = _build_app(
            audience="klai-connector",
            introspect_return={
                "active": True,
                "aud": ["klai-api", "klai-connector"],  # connector present in list
                "urn:zitadel:iam:user:resourceowner:id": "org-abc",
            },
            monkeypatch=monkeypatch,
        )
        resp = client.get("/resource", headers={"Authorization": "Bearer list-aud-token"})
        assert resp.status_code == 200

    def test_org_id_attached_to_request_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """org_id is extracted from the resourceowner claim after audience pass."""
        app = FastAPI()
        settings = SimpleNamespace(
            zitadel_introspection_url="https://example.test/oauth/v2/introspect",
            zitadel_client_id="cid",
            zitadel_client_secret="csecret",
            portal_caller_secret="",
            zitadel_api_audience="klai-connector",
        )

        @app.get("/resource")
        async def resource(request: Request) -> dict[str, str]:
            return {"org_id": request.state.org_id}

        app.add_middleware(AuthMiddleware, settings=settings)
        monkeypatch.setattr(
            AuthMiddleware,
            "_introspect",
            _AsyncIntrospectStub(
                {
                    "active": True,
                    "aud": "klai-connector",
                    "urn:zitadel:iam:user:resourceowner:id": "expected-org-id",
                }
            ),
        )
        client = TestClient(app)
        resp = client.get("/resource", headers={"Authorization": "Bearer good-token"})
        assert resp.status_code == 200
        assert resp.json()["org_id"] == "expected-org-id"
