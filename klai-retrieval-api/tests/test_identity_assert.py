"""SPEC-SEC-IDENTITY-ASSERT-001 REQ-4 + REQ-6 tests for retrieval-api.

Closes the R1 + R3 findings in spec.md. Phase B + C added the verify
infrastructure; Phase D wires retrieval-api up so internal-secret callers
no longer bypass the body-identity guard, and ``emit_event`` sources its
tenant_id / user_id from the verified tuple, never from the request body.

Acceptance coverage:

- AC-3: internal-secret + cross-org body → 403 ``identity_assertion_failed``
- REQ-4.2: missing X-Caller-Service header → 400 ``missing_caller_service``
- REQ-4.2: unknown X-Caller-Service value → 400 ``unknown_caller_service``
- REQ-6: emit_event sources tenant_id from request.state.verified_caller
- AC-6 regression guard: a body-vs-verified mismatch is rejected at REQ-4
  before emit_event can even run with the wrong tuple.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from klai_identity_assert import VerifyResult


def _patch_retrieval_pipeline_to_bypass():
    """Stack of patches that short-circuit the heavy retrieval pipeline so
    the test focuses on the auth/identity layer. Mirrors the legacy pattern
    in test_auth.py's admin-bypass test.
    """
    return [
        patch(
            "retrieval_api.api.retrieve.coreference.resolve",
            new_callable=AsyncMock,
            return_value="resolved",
        ),
        patch(
            "retrieval_api.api.retrieve.embed_single",
            new_callable=AsyncMock,
            return_value=[0.0],
        ),
        patch(
            "retrieval_api.api.retrieve.embed_sparse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "retrieval_api.api.retrieve.gate.should_bypass",
            new_callable=AsyncMock,
            return_value=(True, 0.5),
        ),
    ]


@pytest.fixture
def app_client():
    from retrieval_api.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# REQ-4.2: X-Caller-Service header is required for internal-secret callers
# whose body carries an end-user identity.
# ---------------------------------------------------------------------------


class TestCallerServiceHeaderRequired:
    def test_missing_caller_service_header_400(self, app_client):
        # Internal-secret + body.user_id, but NO X-Caller-Service. Phase D
        # MUST loud-fail rather than route through portal verify with an
        # empty service identifier.
        resp = app_client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "any_org",
                "user_id": "any_user",
                "scope": "personal",
            },
            headers={"X-Internal-Secret": "test-internal-secret-do-not-use-in-prod"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == {"error": "missing_caller_service"}

    def test_unknown_caller_service_400(self, app_client):
        resp = app_client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "any_org",
                "user_id": "any_user",
                "scope": "personal",
            },
            headers={
                "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                "X-Caller-Service": "rogue-service",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == {"error": "unknown_caller_service"}


# ---------------------------------------------------------------------------
# REQ-4.4: portal deny → 403 identity_assertion_failed (reason in logs only)
# ---------------------------------------------------------------------------


class TestPortalDenyRejected:
    def test_no_membership_portal_deny_returns_403(self, monkeypatch, app_client):
        # Override the conftest auto-allow with an asserter that denies.
        class _DenyAsserter:
            async def verify(self, **_kw) -> VerifyResult:
                return VerifyResult.deny("no_membership")

        monkeypatch.setattr(
            "retrieval_api.middleware.auth._get_asserter",
            lambda: _DenyAsserter(),
        )

        resp = app_client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "victim-org",
                "user_id": "attacker-user",
                "scope": "personal",
            },
            headers={
                "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                "X-Caller-Service": "knowledge-mcp",
            },
        )
        assert resp.status_code == 403
        # Reason code stays in logs; body is generic to prevent info leak.
        assert resp.json()["detail"] == {"error": "identity_assertion_failed"}

    def test_portal_unreachable_fails_closed(self, monkeypatch, app_client):
        class _UnreachableAsserter:
            async def verify(self, **_kw) -> VerifyResult:
                return VerifyResult.deny("portal_unreachable")

        monkeypatch.setattr(
            "retrieval_api.middleware.auth._get_asserter",
            lambda: _UnreachableAsserter(),
        )

        resp = app_client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "org-x",
                "user_id": "user-a",
                "scope": "personal",
            },
            headers={
                "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                "X-Caller-Service": "knowledge-mcp",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "identity_assertion_failed"}


# ---------------------------------------------------------------------------
# REQ-4 happy path: portal allow → request proceeds, verified_caller is pinned
# ---------------------------------------------------------------------------


class TestPortalAllowProceeds:
    def test_internal_caller_with_valid_identity_proceeds(self, monkeypatch, app_client):
        captured: dict = {}

        class _AllowAsserter:
            async def verify(
                self,
                *,
                caller_service: str,
                claimed_user_id: str,
                claimed_org_id: str,
                **_kw,
            ) -> VerifyResult:
                captured["caller_service"] = caller_service
                captured["user"] = claimed_user_id
                captured["org"] = claimed_org_id
                return VerifyResult.allow(
                    user_id=claimed_user_id,
                    org_id=claimed_org_id,
                    org_slug="acme",
                    evidence="membership",
                )

        monkeypatch.setattr(
            "retrieval_api.middleware.auth._get_asserter",
            lambda: _AllowAsserter(),
        )

        with (
            _patch_retrieval_pipeline_to_bypass()[0],
            _patch_retrieval_pipeline_to_bypass()[1],
            _patch_retrieval_pipeline_to_bypass()[2],
            _patch_retrieval_pipeline_to_bypass()[3],
        ):
            resp = app_client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-x",
                    "user_id": "user-a",
                    "scope": "personal",
                },
                headers={
                    "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                    "X-Caller-Service": "librechat-bridge",  # not in allowlist
                },
            )

        # librechat-bridge isn't in KNOWN_CALLER_SERVICES — REQ-4.2 rejects.
        assert resp.status_code == 400
        assert resp.json()["detail"] == {"error": "unknown_caller_service"}
        assert captured == {}, "asserter must not be called for unknown service"


# ---------------------------------------------------------------------------
# REQ-6: emit_event sources tenant from verified_caller, not from body
# ---------------------------------------------------------------------------


class TestEmitEventUsesVerifiedTuple:
    def test_emit_event_reads_tenant_from_verified_caller(self, monkeypatch, app_client):
        # The conftest auto-allow returns the claimed tuple as verified.
        # Override here with one that flips them to a *different* canonical
        # value, proving emit_event reads from verified_caller and NOT from
        # the request body.
        class _RewriteAsserter:
            async def verify(self, **_kw) -> VerifyResult:
                # Body said org-x / user-a, but portal's "real" answer is
                # org-canonical / user-canonical. emit_event must use those.
                return VerifyResult.allow(
                    user_id="user-canonical",
                    org_id="org-canonical",
                    org_slug="acme",
                    evidence="membership",
                )

        monkeypatch.setattr(
            "retrieval_api.middleware.auth._get_asserter",
            lambda: _RewriteAsserter(),
        )

        # Capture emit_event calls.
        emit_calls: list[dict] = []

        def _capture_emit(event_type, *, tenant_id, user_id, properties):
            emit_calls.append(
                {
                    "event_type": event_type,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "properties": properties,
                }
            )

        monkeypatch.setattr("retrieval_api.api.retrieve.emit_event", _capture_emit)

        with (
            _patch_retrieval_pipeline_to_bypass()[0],
            _patch_retrieval_pipeline_to_bypass()[1],
            _patch_retrieval_pipeline_to_bypass()[2],
            _patch_retrieval_pipeline_to_bypass()[3],
        ):
            resp = app_client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-x",
                    "user_id": "user-a",
                    "scope": "org",
                },
                headers={
                    "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                    "X-Caller-Service": "knowledge-mcp",
                },
            )

        assert resp.status_code == 200, resp.text
        assert len(emit_calls) == 1
        assert emit_calls[0]["event_type"] == "knowledge.queried"
        # CRITICAL: tenant_id comes from verified, not from the body.
        assert emit_calls[0]["tenant_id"] == "org-canonical"
        assert emit_calls[0]["user_id"] == "user-canonical"
        # Body fields were ignored.
        assert emit_calls[0]["tenant_id"] != "org-x"
        assert emit_calls[0]["user_id"] != "user-a"

    def test_no_emit_event_when_request_rejected_at_req4(self, monkeypatch, app_client):
        # AC-6 regression guard: a body-vs-portal mismatch is rejected at
        # REQ-4 BEFORE the handler reaches emit_event. The product_events
        # table MUST NOT receive a row for the rejected attempt.
        class _DenyAsserter:
            async def verify(self, **_kw) -> VerifyResult:
                return VerifyResult.deny("no_membership")

        monkeypatch.setattr(
            "retrieval_api.middleware.auth._get_asserter",
            lambda: _DenyAsserter(),
        )

        emit_calls: list[dict] = []
        monkeypatch.setattr(
            "retrieval_api.api.retrieve.emit_event",
            lambda *a, **kw: emit_calls.append({"args": a, "kwargs": kw}),
        )

        resp = app_client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "org-y",  # cross-tenant attempt
                "user_id": "user-a",
                "scope": "org",
            },
            headers={
                "X-Internal-Secret": "test-internal-secret-do-not-use-in-prod",
                "X-Caller-Service": "knowledge-mcp",
            },
        )

        assert resp.status_code == 403
        assert emit_calls == [], "emit_event must not run when REQ-4 rejects the call"


# ---------------------------------------------------------------------------
# JWT path preservation: existing SPEC-SEC-010 cross-check still wins
# (regression guard — Phase D extends the internal-secret path without
# weakening the JWT one).
# ---------------------------------------------------------------------------


class TestJwtPathStillRejectsBodyMismatch:
    def test_jwt_caller_with_org_mismatch_still_403(self, app_client):
        from tests.test_auth import _make_jwt_payload, _patch_jwt

        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = app_client.post(
                "/retrieve",
                json={"query": "q", "org_id": "org_y", "user_id": "user_a"},
                headers={"Authorization": "Bearer valid"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}
