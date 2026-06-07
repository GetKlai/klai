"""SPEC-SEC-010 tests for auth middleware, bounds, and cross-user/org guard.

Scope:
  REQ-1 — startup-fail, 401 on missing / invalid secret, JWT path, dual-creds
  REQ-2 — Pydantic Field bounds
  REQ-3 — cross-user / cross-org guard with JWT, admin bypass, internal skip
  REQ-4 — rate limit shape (exceed returns 429 with Retry-After)

External dependencies (real Redis, real Zitadel) are NOT available in CI. Tests
that would require them are either mocked or marked ``skip`` with a clear reason.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_jwt_payload(
    sub: str = "user_a",
    resourceowner: str = "100000000000000001",
    role: str | None = None,
    aud: str = "test-audience",
    scope: str = "klai:internal:retrieval:query",
) -> dict:
    """Return a fake decoded-JWT payload in the Zitadel shape.

    SPEC-SEC-SERVICE-AUTH-001 REQ-3: ``/retrieve`` now requires the
    ``klai:internal:retrieval:query`` scope. The test helper defaults to
    including it so pre-existing tests continue to assert auth-middleware
    behaviour without being blocked by the new scope gate. Tests that want
    to exercise the scope check itself pass ``scope=""`` or a different
    scope explicitly.
    """
    payload: dict = {
        "sub": sub,
        "aud": aud,
        "iss": "https://auth.test.local",
        "urn:zitadel:iam:user:resourceowner:id": resourceowner,
    }
    if scope:
        payload["scope"] = scope
    if role is not None:
        payload["urn:zitadel:iam:org:project:roles"] = {role: {}}
    return payload


def _patch_jwt(payload: dict, error: str | None = None):
    """Patch _decode_jwt to bypass real JWKS / python-jose in unit tests."""

    async def _fake_decode(_token: str):
        return payload, error

    return patch(
        "retrieval_api.middleware.auth._decode_jwt",
        side_effect=_fake_decode,
    )


# --------------------------------------------------------------------------- #
# REQ-1.1 — Startup validator
# --------------------------------------------------------------------------- #


class TestStartupFail:
    """REQ-1.1 — empty INTERNAL_SECRET must abort startup."""

    def test_empty_internal_secret_fails_import(self):
        """Running the config module with INTERNAL_SECRET="" exits non-zero.

        We spawn a subprocess to test the pydantic-settings validator without
        polluting the parent interpreter's already-loaded ``settings`` singleton.
        """
        script = textwrap.dedent(
            """
            import os
            os.environ["INTERNAL_SECRET"] = ""
            os.environ["ZITADEL_ISSUER"] = "https://auth.test.local"
            os.environ["ZITADEL_API_AUDIENCE"] = "test-aud"
            os.environ["REDIS_URL"] = "redis://localhost:6379/0"
            import retrieval_api.config  # must raise
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted input (test-authored script)
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, "Expected non-zero exit on empty INTERNAL_SECRET"
        assert "INTERNAL_SECRET" in (result.stderr + result.stdout)

    def test_whitespace_internal_secret_fails_import(self):
        script = textwrap.dedent(
            """
            import os
            os.environ["INTERNAL_SECRET"] = "   "
            os.environ["ZITADEL_ISSUER"] = "https://auth.test.local"
            os.environ["ZITADEL_API_AUDIENCE"] = "test-aud"
            os.environ["REDIS_URL"] = "redis://localhost:6379/0"
            import retrieval_api.config
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted input (test-authored script)
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0

    def test_missing_zitadel_audience_disables_jwt_not_import(self):
        """Empty ZITADEL_API_AUDIENCE DISABLES the JWT path (graceful degrade) —
        it does NOT fail import. The config validator treats issuer+audience as
        optional: when either is empty, ``jwt_auth_enabled`` is False and all
        callers must use X-Internal-Secret. This is the supported state for the
        internal mesh (SPEC-SEC-SERVICE-AUTH-002 dropped the per-service JWT).
        """
        script = textwrap.dedent(
            """
            import os
            os.environ["INTERNAL_SECRET"] = "ok"
            os.environ["ZITADEL_ISSUER"] = "https://auth.test.local"
            os.environ["ZITADEL_API_AUDIENCE"] = ""
            os.environ["REDIS_URL"] = "redis://localhost:6379/0"
            os.environ["PORTAL_API_URL"] = "http://portal.test.local"
            os.environ["PORTAL_INTERNAL_SECRET"] = "ok"
            import retrieval_api.config as c
            assert c.settings.jwt_auth_enabled is False, "empty audience must disable JWT"
            print("OK import succeeded jwt disabled")
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted input (test-authored script)
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "OK import succeeded jwt disabled" in result.stdout


# --------------------------------------------------------------------------- #
# REQ-1.2 / REQ-1.5 — Internal-secret path
# --------------------------------------------------------------------------- #


class TestInternalSecretPath:
    def test_missing_credentials_rejects_401(self):
        """No X-Internal-Secret and no Authorization → 401."""
        from retrieval_api.main import app

        client = TestClient(app)
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1", "scope": "org"},
        )
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}

    def test_invalid_internal_secret_rejects_401(self):
        from retrieval_api.main import app

        client = TestClient(app)
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1"},
            headers={"X-Internal-Secret": "wrong"},
        )
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}

    def test_valid_internal_secret_accepts(self, client, sample_retrieve_request):
        """Valid X-Internal-Secret → request continues past middleware.

        We patch the downstream pipeline to focus on the auth outcome; a 200
        response (or a route-level validation error) is proof the middleware
        did not reject the request.
        """
        with (
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
        ):
            resp = client.post("/retrieve", json=sample_retrieve_request)
        assert resp.status_code == 200

    def test_health_bypass(self):
        """/health never requires credentials (REQ-1.6)."""
        from retrieval_api.main import app

        client = TestClient(app)
        # The real /health calls external services; we don't assert status_code==200
        # here, only that auth did NOT reject (i.e. not 401).
        resp = client.get("/health")
        assert resp.status_code != 401

    def test_metrics_bypass(self):
        """/metrics never requires credentials (REQ-1.6-adjacent)."""
        from retrieval_api.main import app

        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code != 401


# --------------------------------------------------------------------------- #
# REQ-1.2 / REQ-1.3 — JWT path
# --------------------------------------------------------------------------- #


class TestJwtPath:
    def test_valid_jwt_accepts(self, sample_retrieve_request):
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="100000000000000001")
        sample_retrieve_request["org_id"] = "100000000000000001"
        with (
            _patch_jwt(payload),
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
        ):
            # SPEC-SEC-IDENTITY-ASSERT-003 REQ-1.3: JWT path now requires
            # the X-Org-Id header for portal-side membership lookup.
            resp = client.post(
                "/retrieve",
                json=sample_retrieve_request,
                headers={
                    "Authorization": "Bearer faketoken",
                    "X-Org-Id": "100000000000000001",
                },
            )
        assert resp.status_code == 200

    def test_wrong_audience_rejects_401(self):
        """REQ-8.2: token-confusion — JWT with wrong aud → 401."""
        from retrieval_api.main import app

        client = TestClient(app)
        with _patch_jwt({}, error="invalid_jwt_audience"):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "100000000000000001"},
                headers={"Authorization": "Bearer wrongaudtoken"},
            )
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthorized"}

    def test_expired_jwt_rejects_401(self):
        from retrieval_api.main import app

        client = TestClient(app)
        with _patch_jwt({}, error="expired_jwt"):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "100000000000000001"},
                headers={"Authorization": "Bearer expired"},
            )
        assert resp.status_code == 401

    def test_invalid_signature_rejects_401(self):
        from retrieval_api.main import app

        client = TestClient(app)
        with _patch_jwt({}, error="invalid_jwt_signature"):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "100000000000000001"},
                headers={"Authorization": "Bearer bogus"},
            )
        assert resp.status_code == 401

    def test_both_credentials_prefers_jwt(self):
        """Both valid → JWT path taken (X-Org-Id-driven portal verify applies).

        SPEC-SEC-IDENTITY-ASSERT-003 REQ-1: JWT-bound calls source
        claimed_org_id from X-Org-Id and resolve org via portal. Body
        org_id MUST match the portal-verified org. Mismatch → 403.

        Test scenario: JWT for user_a + X-Org-Id=org_x (portal allows,
        echoes org_x back) but body claims org_y → 403 org_mismatch.
        If the internal-secret path were taken instead, the body
        org_id would have been the claimed value to portal and the
        check would not fire.
        """
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "org_y"},
                headers={
                    "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
                    "Authorization": "Bearer valid",
                    "X-Org-Id": "org_x",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}


# --------------------------------------------------------------------------- #
# REQ-3 — Cross-user / cross-org guard
# --------------------------------------------------------------------------- #


class TestCrossUserOrgGuard:
    def test_cross_org_rejected_403(self):
        """REQ-8.4 + SPEC-003 REQ-1.5: X-Org-Id=org_x (portal allows),
        body org_id=org_y → 403 org_mismatch (defence-in-depth body check)."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "org_y", "user_id": "user_a"},
                headers={"Authorization": "Bearer valid", "X-Org-Id": "org_x"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}

    def test_cross_user_rejected_403(self):
        """REQ-8.3: JWT sub=user_a, body user_id=user_b → 403."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org_x",
                    "user_id": "user_b",
                    "scope": "personal",
                },
                headers={"Authorization": "Bearer valid"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "user_mismatch"}

    def test_cross_user_response_does_not_echo_values(self):
        """REQ-3.1 / REQ-3.2: response body never echoes caller-supplied values."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org_x",
                    "user_id": "victim_user_b",
                    "scope": "personal",
                },
                headers={"Authorization": "Bearer valid"},
            )
        body_text = resp.text
        assert "user_a" not in body_text
        assert "victim_user_b" not in body_text

    def test_admin_role_bypasses_check(self, sample_retrieve_request):
        """REQ-3.1 / REQ-3.2: admin role bypasses the cross-user/org check."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="admin_user", resourceowner="org_admin", role="admin")
        sample_retrieve_request["org_id"] = "other_org"
        with (
            _patch_jwt(payload),
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
        ):
            resp = client.post(
                "/retrieve",
                json=sample_retrieve_request,
                headers={"Authorization": "Bearer admin"},
            )
        assert resp.status_code == 200

    def test_admin_org_scope_without_user_emits_tenant_event(
        self, sample_retrieve_request, monkeypatch
    ):
        """Admin org-scope calls without user_id still pin tenant identity for events."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="admin_user", resourceowner="org_admin", role="admin")
        sample_retrieve_request["org_id"] = "other_org"
        sample_retrieve_request.pop("user_id", None)

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
            _patch_jwt(payload),
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
        ):
            resp = client.post(
                "/retrieve",
                json=sample_retrieve_request,
                headers={"Authorization": "Bearer admin"},
            )

        assert resp.status_code == 200, resp.text
        assert emit_calls == [
            {
                "event_type": "knowledge.queried",
                "tenant_id": "other_org",
                "user_id": None,
                "properties": {
                    "scope": "org",
                    "kb_slugs": [],
                    "had_results": False,
                    "result_count": 0,
                },
            }
        ]

    def test_non_admin_jwt_does_not_bypass_cross_org(self):
        """SPEC-SEC-TENANT-001 REQ-4.1 / REQ-5.3 / A-3 — non-admin JWT cross-org -> 403.

        Under the v0.5.0 mapping, non-admin invites (group-admin, member)
        receive no Zitadel project-role grant. Their JWTs carry NO
        ``urn:zitadel:iam:org:project:roles`` claim. ``_extract_role``
        returns None; ``auth.role`` is None; the cross-org check fires.

        This test pins the contract: a member-shaped JWT (no roles claim)
        whose ``resourceowner`` differs from the body ``org_id`` MUST
        receive 403, never 200 by accidental admin-equivalence.
        """
        from retrieval_api.main import app

        client = TestClient(app)
        # role=None ⇒ helper omits the urn:zitadel:iam:org:project:roles key
        # entirely. This matches the production v0.5.0 shape for invitees
        # whose portal_users.role is "group-admin" or "member".
        payload = _make_jwt_payload(sub="user-member-1", resourceowner="org-a", role=None)
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-b",
                    "user_id": "user-member-1",
                },
                # X-Org-Id=org-a (caller asserts they want to act on org-a;
                # portal stub allows). Body claims org-b → defence-in-depth
                # body-vs-verified mismatch fires.
                headers={"Authorization": "Bearer valid", "X-Org-Id": "org-a"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}

    def test_org_admin_role_is_no_longer_admin_equivalent(self):
        """SPEC-SEC-TENANT-001 REQ-4.1 — `org_admin` removed from admin-set.

        Pre-v0.5.0 ``_extract_role`` matched both ``admin`` AND
        ``org_admin`` as admin-equivalent. The ``org_admin`` branch was
        unreachable in any production flow but represented a latent
        attack surface — a future code path that ever produced the key
        (SCIM provisioner, migration script, manual Zitadel poke) would
        have silently granted cross-org bypass.

        v0.5.0 REQ-4.1 removes the ``org_admin`` branch. This test pins
        the removal: a JWT carrying ``{"org_admin": {}}`` whose
        ``resourceowner`` differs from the body ``org_id`` MUST receive
        403.
        """
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user-x", resourceowner="org-a", role="org_admin")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-b",
                    "user_id": "user-x",
                },
                headers={"Authorization": "Bearer valid", "X-Org-Id": "org-a"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}

    def test_internal_secret_caller_now_verified_against_portal(self, client):
        """SPEC-SEC-IDENTITY-ASSERT-001 REQ-4: internal-secret callers no
        longer bypass the body-identity guard. They are re-verified against
        portal-api with the X-Caller-Service header.

        The conftest auto-mock returns allow for any (user, org) tuple, so
        this test asserts the happy-path flow still completes — the real
        guard's failure modes are exercised in
        ``tests/test_identity_assert.py``.
        """
        with (
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
        ):
            # Arbitrary org_id / user_id — internal caller is authoritative.
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "any_org", "user_id": "any_user"},
            )
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# REQ-2 — Pydantic bounds
# --------------------------------------------------------------------------- #


class TestBounds:
    """REQ-2.1 … REQ-2.5 — Pydantic Field bounds on RetrieveRequest."""

    def test_top_k_over_limit_422(self, client):
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1", "top_k": 1000},
        )
        assert resp.status_code == 422
        assert "top_k" in resp.text

    def test_top_k_zero_422(self, client):
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1", "top_k": 0},
        )
        assert resp.status_code == 422

    def test_conversation_history_too_long_422(self, client):
        history = [{"role": "user", "content": "x"} for _ in range(21)]
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1", "conversation_history": history},
        )
        assert resp.status_code == 422

    def test_conversation_content_too_long_422(self, client):
        history = [{"role": "user", "content": "x" * 10_000}]
        resp = client.post(
            "/retrieve",
            json={"query": "q", "org_id": "org-1", "conversation_history": history},
        )
        assert resp.status_code == 422

    def test_kb_slugs_too_long_422(self, client):
        resp = client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "org-1",
                "kb_slugs": [f"kb-{i}" for i in range(21)],
            },
        )
        assert resp.status_code == 422

    def test_taxonomy_node_ids_too_long_422(self, client):
        resp = client.post(
            "/retrieve",
            json={
                "query": "q",
                "org_id": "org-1",
                "taxonomy_node_ids": list(range(51)),
            },
        )
        assert resp.status_code == 422

    def test_valid_bounds_accepted(self, client, sample_retrieve_request):
        """Request inside all bounds is accepted through the bounds layer."""
        sample_retrieve_request["top_k"] = 50
        sample_retrieve_request["conversation_history"] = [
            {"role": "user", "content": "hi"} for _ in range(20)
        ]
        sample_retrieve_request["kb_slugs"] = [f"kb-{i}" for i in range(20)]
        sample_retrieve_request["taxonomy_node_ids"] = list(range(50))
        with (
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
        ):
            resp = client.post("/retrieve", json=sample_retrieve_request)
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# REQ-4 — Rate limit (shape)
# --------------------------------------------------------------------------- #


class TestRateLimit:
    """REQ-4 — rate limiter returns 429 + Retry-After when over the limit.

    A full Redis-backed flood test is marked ``skip`` because CI does not have
    a reachable Redis. We validate the 429 shape by patching the limiter.
    """

    def test_limiter_blocks_over_limit(self, client, sample_retrieve_request):
        """When check_and_increment denies → 429 with Retry-After."""

        async def _deny(*_a, **_kw):
            return False, 42

        with patch("retrieval_api.middleware.auth.check_and_increment", side_effect=_deny):
            resp = client.post("/retrieve", json=sample_retrieve_request)
        assert resp.status_code == 429
        assert resp.json() == {"error": "rate_limit_exceeded"}
        assert resp.headers.get("retry-after") == "42"

    @pytest.mark.skip(
        reason=(
            "Real Redis-backed 601-request flood requires a reachable Redis "
            "instance; the logical behaviour is covered by "
            "test_limiter_blocks_over_limit, and a full integration flood will "
            "be run in the staging smoke phase (REQ-9.2)."
        )
    )
    def test_601_requests_in_60s_returns_429(self):  # pragma: no cover - skipped
        raise AssertionError("integration-only")


# --------------------------------------------------------------------------- #
# REQ-1.5 — Compare path uses hmac.compare_digest (no literal ==)
# --------------------------------------------------------------------------- #


def test_auth_module_uses_hmac_compare_digest():
    """Static guard: the secret-compare path MUST use hmac.compare_digest.

    A regression that re-introduces ``==`` for the internal-secret comparison
    would fail this test; we do a simple source scan because the middleware is
    a single small module.
    """
    mod = importlib.import_module("retrieval_api.middleware.auth")
    src = open(mod.__file__, encoding="utf-8").read()
    assert "hmac.compare_digest" in src
    # No ``==`` comparison against settings.internal_secret anywhere.
    assert "== settings.internal_secret" not in src
    assert "settings.internal_secret ==" not in src


# --------------------------------------------------------------------------- #
# SPEC-SEC-IDENTITY-ASSERT-003 — JWT-without-resourceowner regression guards
# --------------------------------------------------------------------------- #


class TestSpec003JwtWithoutResourceowner:
    """SPEC-SEC-IDENTITY-ASSERT-003 acceptance group A.

    The Klai BFF requests scope `openid profile email offline_access`
    which does NOT emit `urn:zitadel:iam:user:resourceowner:id`. After
    SPEC-003 the retrieval-api JWT path no longer reads the claim;
    org-resolution flows through portal /internal/identity/verify
    keyed on Zitadel sub + portal_users membership.
    """

    def test_jwt_lacking_resourceowner_claim_works(self, sample_retrieve_request):
        """A1: JWT without the resourceowner claim → 200 when X-Org-Id
        names a real membership and the portal stub allows."""
        from retrieval_api.main import app

        client = TestClient(app)
        # Build a JWT payload that does NOT include the
        # urn:zitadel:iam:user:resourceowner:id claim.
        payload = {
            "sub": "user_a",
            "aud": "test-audience",
            "iss": "https://auth.test.local",
            "scope": "klai:internal:retrieval:query",
        }
        sample_retrieve_request["org_id"] = "100000000000000001"
        with (
            _patch_jwt(payload),
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
        ):
            resp = client.post(
                "/retrieve",
                json=sample_retrieve_request,
                headers={
                    "Authorization": "Bearer faketoken",
                    "X-Org-Id": "100000000000000001",
                },
            )
        assert resp.status_code == 200

    def test_missing_x_org_id_header_returns_400(self):
        """REQ-1.4: JWT path without X-Org-Id is a config error, not a
        silent fail-open. Must be 400 with `missing_org_id`."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "100000000000000001",
                    "user_id": "user_a",
                },
                headers={"Authorization": "Bearer valid"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == {"error": "missing_org_id"}

    def test_empty_x_org_id_header_returns_400(self):
        """Empty-string X-Org-Id treated identically to missing."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "100000000000000001",
                    "user_id": "user_a",
                },
                headers={"Authorization": "Bearer valid", "X-Org-Id": ""},
            )
        assert resp.status_code == 400

    def test_portal_deny_returns_403_identity_assertion_failed(self):
        """REQ-1.5: portal returns deny → 403 identity_assertion_failed."""
        from retrieval_api.main import app

        client = TestClient(app)

        class _DenyAsserter:
            async def verify(self, **_kwargs):
                from klai_identity_assert import VerifyResult

                return VerifyResult.deny("no_membership")

        payload = _make_jwt_payload(sub="user_a")
        with (
            _patch_jwt(payload),
            patch(
                "retrieval_api.middleware.auth._get_asserter",
                lambda: _DenyAsserter(),
            ),
        ):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "100000000000000001",
                    "user_id": "user_a",
                },
                headers={
                    "Authorization": "Bearer valid",
                    "X-Org-Id": "100000000000000001",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "identity_assertion_failed"}

    def test_no_resourceowner_constant_in_module(self):
        """REQ-1.2 + REQ-3 ast-grep guard: the literal claim string MUST
        NOT appear in the production source. Fixtures and comments in
        tests are exempt; this test scans the production module only."""
        import importlib

        mod = importlib.import_module("retrieval_api.middleware.auth")
        with open(mod.__file__, encoding="utf-8") as f:
            src = f.read()
        # Acceptable: comment lines explaining WHY we don't read the
        # claim. Unacceptable: any actual `claims.get(...)` of the claim
        # or `_ZITADEL_RESOURCEOWNER_CLAIM` constant assignment.
        assert "_ZITADEL_RESOURCEOWNER_CLAIM = " not in src
        assert 'payload.get("urn:zitadel:iam:user:resourceowner:id"' not in src
        assert 'claims.get("urn:zitadel:iam:user:resourceowner:id"' not in src

    def test_authcontext_has_no_resourceowner_field(self):
        """REQ-1.1: AuthContext.resourceowner is gone."""
        from dataclasses import fields

        from retrieval_api.middleware.auth import AuthContext

        names = {f.name for f in fields(AuthContext)}
        assert "resourceowner" not in names
        # Sanity: the rest of the contract is intact.
        assert {"method", "sub", "role", "scopes", "bearer_token"}.issubset(names)
