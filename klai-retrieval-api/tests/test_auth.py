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
    resourceowner: str = "362757920133283846",
    role: str | None = None,
    aud: str = "test-audience",
) -> dict:
    """Return a fake decoded-JWT payload in the Zitadel shape."""
    payload: dict = {
        "sub": sub,
        "aud": aud,
        "iss": "https://auth.test.local",
        "urn:zitadel:iam:user:resourceowner:id": resourceowner,
    }
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

    def test_missing_zitadel_audience_fails_import(self):
        script = textwrap.dedent(
            """
            import os
            os.environ["INTERNAL_SECRET"] = "ok"
            os.environ["ZITADEL_ISSUER"] = "https://auth.test.local"
            os.environ["ZITADEL_API_AUDIENCE"] = ""
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
        assert "ZITADEL_API_AUDIENCE" in (result.stderr + result.stdout)


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
        payload = _make_jwt_payload(sub="user_a", resourceowner="362757920133283846")
        sample_retrieve_request["org_id"] = "362757920133283846"
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
                headers={"Authorization": "Bearer faketoken"},
            )
        assert resp.status_code == 200

    def test_wrong_audience_rejects_401(self):
        """REQ-8.2: token-confusion — JWT with wrong aud → 401."""
        from retrieval_api.main import app

        client = TestClient(app)
        with _patch_jwt({}, error="invalid_jwt_audience"):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "362757920133283846"},
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
                json={"query": "q", "org_id": "362757920133283846"},
                headers={"Authorization": "Bearer expired"},
            )
        assert resp.status_code == 401

    def test_invalid_signature_rejects_401(self):
        from retrieval_api.main import app

        client = TestClient(app)
        with _patch_jwt({}, error="invalid_jwt_signature"):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "362757920133283846"},
                headers={"Authorization": "Bearer bogus"},
            )
        assert resp.status_code == 401

    def test_both_credentials_prefers_jwt(self):
        """Both valid → JWT path taken (cross-user/org guard applies)."""
        from retrieval_api.main import app

        client = TestClient(app)
        # JWT resourceowner='org_x' but body org_id='org_y' — JWT path rejects.
        # If internal-secret path were taken instead, the request would succeed.
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "org_y"},
                headers={
                    "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
                    "Authorization": "Bearer valid",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}


# --------------------------------------------------------------------------- #
# REQ-3 — Cross-user / cross-org guard
# --------------------------------------------------------------------------- #


class TestCrossUserOrgGuard:
    def test_cross_org_rejected_403(self):
        """REQ-8.4: JWT resourceowner=org_x, body org_id=org_y → 403."""
        from retrieval_api.main import app

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={"query": "q", "org_id": "org_y", "user_id": "user_a"},
                headers={"Authorization": "Bearer valid"},
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
        payload = _make_jwt_payload(
            sub="admin_user", resourceowner="org_admin", role="admin"
        )
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
        payload = _make_jwt_payload(
            sub="user-member-1", resourceowner="org-a", role=None
        )
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-b",
                    "user_id": "user-member-1",
                },
                headers={"Authorization": "Bearer valid"},
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
        payload = _make_jwt_payload(
            sub="user-x", resourceowner="org-a", role="org_admin"
        )
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "q",
                    "org_id": "org-b",
                    "user_id": "user-x",
                },
                headers={"Authorization": "Bearer valid"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "org_mismatch"}

    def test_internal_secret_skips_cross_check(self, client):
        """REQ-3.3 / REQ-8.7: internal secret caller bypasses cross-user/org check."""
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

        with patch(
            "retrieval_api.middleware.auth.check_and_increment", side_effect=_deny
        ):
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
