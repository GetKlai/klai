"""SPEC-SEC-IDENTITY-ASSERT-003 REQ-2 regression guards for klai-connector.

Pinned invariants:
- B1: a token whose introspection response lacks the resourceowner claim
  but has a valid sub + matching X-Org-Id → 200 (org_id resolved by portal).
- B2: missing X-Org-Id header on JWT path → 400 missing_org_id.
- B3: portal denies → 403 identity_assertion_failed (NOT 401).
- B4: portal-resolved org_id (NOT JWT claim) is pinned on request.state.
- B5: literal claim string is gone from production source.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.middleware import auth as auth_module
from app.middleware.auth import AuthMiddleware


class _AsyncIntrospectStub:
    def __init__(self, return_value: dict[str, Any] | None) -> None:
        self._rv = return_value

    async def __call__(self, token: str) -> dict[str, Any] | None:  # noqa: ARG002
        return self._rv


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        zitadel_introspection_url="https://example.test/oauth/v2/introspect",
        zitadel_client_id="cid",
        zitadel_client_secret="csecret",
        portal_caller_secret="",
        zitadel_api_audience="klai-connector",
        portal_api_url="http://portal-api.test:8100",
        portal_internal_secret="test-internal-secret",  # noqa: S106
    )


def _build_app(monkeypatch: pytest.MonkeyPatch, *, deny: bool = False, org_id: str = "org-resolved"):
    app = FastAPI()

    @app.get("/resource")
    async def resource(request: Request) -> dict[str, str]:
        return {"org_id": str(getattr(request.state, "org_id", "<unset>"))}

    app.add_middleware(AuthMiddleware, settings=_settings())

    monkeypatch.setattr(
        AuthMiddleware,
        "_introspect",
        _AsyncIntrospectStub(
            {
                "active": True,
                "aud": "klai-connector",
                "sub": "test-user-sub",
                # Deliberately NO urn:zitadel:iam:user:resourceowner:id —
                # this is the regression-guard scenario that failed
                # silently before SPEC-003.
            }
        ),
    )

    from klai_identity_assert import VerifyResult

    class _StubAsserter:
        async def verify(self, **_kwargs: Any) -> VerifyResult:
            if deny:
                return VerifyResult.deny("no_membership")
            return VerifyResult.allow(
                user_id="test-user-sub",
                org_id=org_id,
                org_slug="test-slug",
                evidence="membership",
            )

    monkeypatch.setattr(auth_module, "_asserter", None)
    monkeypatch.setattr(auth_module, "_get_asserter", lambda _settings: _StubAsserter())
    return TestClient(app)


class TestSpec003ResourceownerDropped:
    def test_jwt_without_resourceowner_works_when_portal_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(monkeypatch, org_id="org-resolved")
        resp = client.get(
            "/resource",
            headers={"Authorization": "Bearer good-token", "X-Org-Id": "org-resolved"},
        )
        assert resp.status_code == 200
        # B4: pinned org_id is the portal-resolved value.
        assert resp.json()["org_id"] == "org-resolved"

    def test_missing_x_org_id_returns_400_missing_org_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(monkeypatch)
        resp = client.get("/resource", headers={"Authorization": "Bearer good-token"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_org_id"

    def test_empty_x_org_id_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(monkeypatch)
        resp = client.get(
            "/resource",
            headers={"Authorization": "Bearer good-token", "X-Org-Id": ""},
        )
        assert resp.status_code == 400

    def test_portal_deny_returns_403_identity_assertion_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _build_app(monkeypatch, deny=True)
        resp = client.get(
            "/resource",
            headers={"Authorization": "Bearer good-token", "X-Org-Id": "org-victim"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "identity_assertion_failed"

    def test_no_resourceowner_claim_string_in_module_source(self) -> None:
        """B5: the literal claim string is removed from production source.
        Comments may still mention the claim; this regression guard checks
        the actual code paths."""
        import importlib

        mod = importlib.import_module("app.middleware.auth")
        with open(mod.__file__, encoding="utf-8") as f:
            src = f.read()
        assert 'claims.get("urn:zitadel:iam:user:resourceowner:id"' not in src

    def test_portal_bypass_path_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """REQ-2.7: portal-api service-to-service bypass unchanged."""
        app = FastAPI()

        @app.get("/resource")
        async def resource(request: Request) -> dict[str, Any]:
            return {
                "from_portal": getattr(request.state, "from_portal", False),
                "org_id": getattr(request.state, "org_id", "<unset>"),
            }

        s = SimpleNamespace(
            zitadel_introspection_url="https://example.test/oauth/v2/introspect",
            zitadel_client_id="cid",
            zitadel_client_secret="csecret",
            portal_caller_secret="portal-shared",
            zitadel_api_audience="klai-connector",
            portal_api_url="http://portal-api.test:8100",
            portal_internal_secret="test-internal",  # noqa: S106
        )
        app.add_middleware(AuthMiddleware, settings=s)
        client = TestClient(app)
        resp = client.get("/resource", headers={"Authorization": "Bearer portal-shared"})
        assert resp.status_code == 200
        assert resp.json()["from_portal"] is True
        # Bypass keeps org_id unset (None) per existing contract.
        assert resp.json()["org_id"] in (None, "None")
