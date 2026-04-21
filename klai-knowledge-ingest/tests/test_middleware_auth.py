"""SPEC-SEC-011 tests — fail-closed auth for knowledge-ingest.

Coverage:
  * REQ-1 / REQ-5.1 — startup validator rejects empty/missing secret
  * REQ-2 / REQ-5.3 — middleware returns 401 for missing/wrong/empty header
  * REQ-3 / REQ-5.4 — every ``routes/ingest.py`` handler guarded by
    ``_verify_internal_secret`` returns 401 independent of the middleware
  * REQ-5.5 — wrong-length headers are compared without crashing
    (:func:`hmac.compare_digest` tolerance)
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-value-123"


# --------------------------------------------------------------------------- #
# REQ-1 / REQ-5.1 — Startup validator
# --------------------------------------------------------------------------- #


class TestStartupFailsOnEmptySecret:
    """Import-time validation: Settings() raises when the secret is empty.

    We run in a subprocess so the parent interpreter's already-loaded
    ``settings`` singleton (via conftest) is not disturbed.
    """

    def test_empty_knowledge_ingest_secret_fails_import(self):
        script = textwrap.dedent(
            """
            import os
            os.environ["KNOWLEDGE_INGEST_SECRET"] = ""
            import knowledge_ingest.config  # must raise during Settings()
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted, test-authored script
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit on empty KNOWLEDGE_INGEST_SECRET"
        )
        assert "KNOWLEDGE_INGEST_SECRET" in (result.stderr + result.stdout)

    def test_whitespace_knowledge_ingest_secret_fails_import(self):
        script = textwrap.dedent(
            """
            import os
            os.environ["KNOWLEDGE_INGEST_SECRET"] = "   "
            import knowledge_ingest.config
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted, test-authored script
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "KNOWLEDGE_INGEST_SECRET" in (result.stderr + result.stdout)

    def test_missing_knowledge_ingest_secret_fails_import(self):
        script = textwrap.dedent(
            """
            import os
            os.environ.pop("KNOWLEDGE_INGEST_SECRET", None)
            import knowledge_ingest.config
            """
        )
        result = subprocess.run(  # noqa: S603 — trusted, test-authored script
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "KNOWLEDGE_INGEST_SECRET" in (result.stderr + result.stdout)

    def test_valid_secret_allows_construction(self):
        """In-process sanity check — Settings constructs with a valid secret."""
        from pydantic_settings import BaseSettings  # noqa: F401 — ensures stack

        from knowledge_ingest.config import Settings

        s = Settings(knowledge_ingest_secret="valid-secret")  # noqa: S106
        assert s.knowledge_ingest_secret == "valid-secret"


# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def secured_client():
    """TestClient for the real app with a valid secret configured.

    ``conftest.py`` already exports ``KNOWLEDGE_INGEST_SECRET`` so
    ``settings.knowledge_ingest_secret`` already equals :data:`TEST_SECRET`.
    We still patch the middleware import and a few startup side effects so the
    client can boot without Qdrant/Postgres.
    """
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)

    with (
        patch(
            "knowledge_ingest.qdrant_store.ensure_collection",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.db.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# --------------------------------------------------------------------------- #
# REQ-2 / REQ-5.3 / REQ-5.5 — Middleware enforcement
# --------------------------------------------------------------------------- #


class TestMiddlewareEnforcement:
    def test_health_without_header_allowed(self, secured_client):
        """/health is exempt and must not require the secret."""
        mock_resp = MagicMock(status_code=200)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(
            return_value=MagicMock(get=AsyncMock(return_value=mock_resp))
        )
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("knowledge_ingest.app.settings.graphiti_enabled", False),
            patch("qdrant_client.AsyncQdrantClient") as mock_qc,
            patch("httpx.AsyncClient", return_value=mock_ctx),
        ):
            mock_qc.return_value.get_collections = AsyncMock(return_value=[])
            resp = secured_client.get("/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_missing_header_returns_401(self, secured_client):
        resp = secured_client.post(
            "/ingest/v1/document",
            json={
                "org_id": "org1",
                "kb_slug": "test",
                "path": "test.md",
                "content": "hello",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid or missing X-Internal-Secret"

    def test_empty_header_returns_401(self, secured_client):
        resp = secured_client.post(
            "/ingest/v1/document",
            json={
                "org_id": "org1",
                "kb_slug": "test",
                "path": "test.md",
                "content": "hello",
            },
            headers={"X-Internal-Secret": ""},
        )
        assert resp.status_code == 401

    def test_wrong_header_returns_401(self, secured_client):
        resp = secured_client.post(
            "/ingest/v1/document",
            json={
                "org_id": "org1",
                "kb_slug": "test",
                "path": "test.md",
                "content": "hello",
            },
            headers={"X-Internal-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_wrong_length_header_returns_401_without_crash(self, secured_client):
        """REQ-5.5: differing-length comparison must not crash —
        :func:`hmac.compare_digest` tolerates mismatched length.
        """
        resp = secured_client.post(
            "/ingest/v1/document",
            json={
                "org_id": "org1",
                "kb_slug": "test",
                "path": "test.md",
                "content": "hello",
            },
            headers={"X-Internal-Secret": "x"},
        )
        assert resp.status_code == 401

    def test_valid_header_passes_middleware(self, secured_client):
        with patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
            return_value={"status": "ok", "chunks": 1, "title": "test"},
        ):
            resp = secured_client.post(
                "/ingest/v1/document",
                json={
                    "org_id": "org1",
                    "kb_slug": "test",
                    "path": "test.md",
                    "content": "hello",
                },
                headers={"X-Internal-Secret": TEST_SECRET},
            )
            assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# REQ-3 / REQ-5.4 — Route-helper enforcement
# --------------------------------------------------------------------------- #

# Each entry is (method, path, json_body_or_none, query_params_or_none).
# The six routes in routes/ingest.py that call ``_verify_internal_secret`` as
# their first statement. A missing/invalid header must yield 401 regardless of
# whether the middleware was hit (the route helper is an independent guard).
_GUARDED_ROUTES = [
    ("DELETE", "/ingest/v1/kb", None, {"org_id": "org1", "kb_slug": "kb1"}),
    (
        "DELETE",
        "/ingest/v1/connector",
        None,
        {"org_id": "org1", "kb_slug": "kb1", "connector_id": "c1"},
    ),
    (
        "PATCH",
        "/ingest/v1/kb/visibility",
        {"org_id": "org1", "kb_slug": "kb1", "visibility": "private"},
        None,
    ),
    (
        "POST",
        "/ingest/v1/kb/webhook",
        {"org_id": "org1", "kb_slug": "kb1", "gitea_repo": "org/repo"},
        None,
    ),
    (
        "DELETE",
        "/ingest/v1/kb/webhook",
        {"org_id": "org1", "kb_slug": "kb1", "gitea_repo": "org/repo"},
        None,
    ),
    (
        "POST",
        "/ingest/v1/kb/sync",
        {"org_id": "org1", "kb_slug": "kb1", "gitea_repo": "org/repo"},
        None,
    ),
]


class TestRouteHelperEnforcement:
    """Every ``_verify_internal_secret`` caller must 401 without a valid header.

    The middleware already rejects at the app boundary, but these tests exercise
    the per-route guard specifically by asserting that removing the fail-open
    branch inside the helper is effective: both layers now deny.
    """

    @pytest.mark.parametrize(
        ("method", "path", "body", "params"),
        _GUARDED_ROUTES,
        ids=[r[1] + ":" + r[0] for r in _GUARDED_ROUTES],
    )
    def test_missing_header_returns_401(
        self, secured_client, method, path, body, params
    ):
        resp = secured_client.request(
            method,
            path,
            json=body,
            params=params,
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("method", "path", "body", "params"),
        _GUARDED_ROUTES,
        ids=[r[1] + ":" + r[0] for r in _GUARDED_ROUTES],
    )
    def test_wrong_header_returns_401(
        self, secured_client, method, path, body, params
    ):
        resp = secured_client.request(
            method,
            path,
            json=body,
            params=params,
            headers={"X-Internal-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# REQ-3 — Route helper unit test (direct call, middleware bypassed)
# --------------------------------------------------------------------------- #


class TestVerifyInternalSecretHelperDirect:
    """Call ``_verify_internal_secret`` directly to prove the guard is in the
    helper itself, not only in the middleware upstream.
    """

    def _fake_request(self, header_value: str | None):
        """Return an object shaped like ``starlette.requests.Request`` for header access."""
        headers: dict[str, str] = {}
        if header_value is not None:
            headers["x-internal-secret"] = header_value
        req = MagicMock()
        req.headers = headers
        return req

    def test_missing_header_raises_401(self):
        from fastapi import HTTPException

        from knowledge_ingest.routes.ingest import _verify_internal_secret

        with pytest.raises(HTTPException) as exc_info:
            _verify_internal_secret(self._fake_request(None))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    def test_empty_header_raises_401(self):
        from fastapi import HTTPException

        from knowledge_ingest.routes.ingest import _verify_internal_secret

        with pytest.raises(HTTPException) as exc_info:
            _verify_internal_secret(self._fake_request(""))
        assert exc_info.value.status_code == 401

    def test_wrong_header_raises_401(self):
        from fastapi import HTTPException

        from knowledge_ingest.routes.ingest import _verify_internal_secret

        with pytest.raises(HTTPException) as exc_info:
            _verify_internal_secret(self._fake_request("nope"))
        assert exc_info.value.status_code == 401

    def test_wrong_length_header_raises_401_without_crash(self):
        """REQ-5.5: differing lengths must not crash the helper."""
        from fastapi import HTTPException

        from knowledge_ingest.routes.ingest import _verify_internal_secret

        with pytest.raises(HTTPException) as exc_info:
            _verify_internal_secret(self._fake_request("x"))
        assert exc_info.value.status_code == 401

    def test_valid_header_does_not_raise(self):
        from knowledge_ingest.routes.ingest import _verify_internal_secret

        # Should return None silently.
        assert _verify_internal_secret(self._fake_request(TEST_SECRET)) is None
