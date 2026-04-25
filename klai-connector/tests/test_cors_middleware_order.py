"""SPEC-SEC-CORS-001 AC-15: CORSMiddleware wraps 401 responses in klai-connector.

The connector middleware stack MUST register CORSMiddleware LAST (outermost in
Starlette LIFO execution order) so that 401 responses emitted by AuthMiddleware
carry the correct ``Access-Control-Allow-Origin`` header for allowed origins.

Two verification layers:
1. Static source-order check on app/main.py — catches wrong registration order
   without instantiating the full app (REQ-6.4).
2. Behaviour test using a test app that mirrors the production middleware stack —
   verifies the 401+CORS contract end-to-end (AC-15 positive + negative).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.middleware.auth import AuthMiddleware

# ---------------------------------------------------------------------------
# Layer 1: Static source-order assertion (REQ-6.4)
# ---------------------------------------------------------------------------

_MAIN_PY = Path(__file__).parent.parent / "app" / "main.py"


class TestMiddlewareSourceOrder:
    """Assert that CORSMiddleware is the LAST add_middleware call in main.py."""

    def test_cors_is_last_add_middleware_call(self) -> None:
        """CORSMiddleware must appear AFTER AuthMiddleware and RequestContextMiddleware.

        Starlette uses LIFO order: the last add_middleware call becomes the
        outermost layer.  CORS must be outermost so 401 responses from
        AuthMiddleware carry CORS headers (SPEC-SEC-CORS-001 REQ-6.4).

        Multi-line calls are supported: we join continuation lines so that
        ``app.add_middleware(`` followed by ``CORSMiddleware,`` on the next
        line is recognised as a single CORSMiddleware registration.
        """
        src = _MAIN_PY.read_text(encoding="utf-8")

        # Find all add_middleware call sites.  A call may span multiple lines,
        # so we capture up to 3 lines starting from the `app.add_middleware(`
        # line and join them into one string for the name check.
        lines = src.splitlines()
        add_middleware_calls: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("app.add_middleware("):
                # Join up to 3 lines to handle multi-line calls.
                joined = " ".join(lines[i : i + 3])
                add_middleware_calls.append((i, joined))

        assert add_middleware_calls, "No app.add_middleware(...) calls found in main.py"

        last_lineno, last_call = add_middleware_calls[-1]
        assert "CORSMiddleware" in last_call, (
            f"Last app.add_middleware call (line {last_lineno + 1}) must be "
            f"CORSMiddleware, but found: {last_call!r}. "
            "Reorder so CORSMiddleware is registered last (outermost) per "
            "SPEC-SEC-CORS-001 REQ-6.4."
        )

    def test_cors_middleware_guard_preserved(self) -> None:
        """The 'if allowed_origins:' guard around CORSMiddleware must still be present."""
        src = _MAIN_PY.read_text(encoding="utf-8")
        assert "if allowed_origins:" in src, (
            "The 'if allowed_origins:' guard must be preserved around CORSMiddleware "
            "registration in app/main.py (connector uses empty-string default in prod)."
        )


# ---------------------------------------------------------------------------
# Layer 2: Behaviour tests using a test-app mirror (AC-15)
# ---------------------------------------------------------------------------


def _make_settings(*, cors_origins: str = "") -> SimpleNamespace:
    """Build the minimal Settings-shape that AuthMiddleware.__init__ reads."""
    return SimpleNamespace(
        zitadel_introspection_url="https://auth.test.local/oauth/v2/introspect",
        zitadel_client_id="test-client-id",
        zitadel_client_secret="test-client-secret",
        portal_caller_secret="",
        zitadel_api_audience="",
        cors_origins=cors_origins,
    )


def _build_test_app(cors_origins: str) -> FastAPI:
    """Create a minimal FastAPI app mirroring the CORRECT connector middleware order.

    Middleware registration order: last-added runs FIRST on the request
    (Starlette LIFO — see .claude/rules/klai/lang/python.md and
    SPEC-SEC-CORS-001 REQ-6). Desired execution: CORS (outermost, wraps 401
    with CORS headers, handles preflight) -> Auth (reject missing header) ->
    route. So we register in reverse: Auth, CORS.
    """
    settings = _make_settings(cors_origins=cors_origins)
    app = FastAPI()

    @app.get("/api/v1/connectors")
    async def connectors() -> dict[str, str]:  # pragma: no cover
        return {"ok": "true"}

    # Register in reverse execution order (Starlette LIFO).
    app.add_middleware(AuthMiddleware, settings=settings)

    allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    return app


class TestCorsWraps401:
    """AC-15 positive: CORSMiddleware is outermost — 401 carries ACAO for allowed origin."""

    _ALLOWED_ORIGIN = "http://localhost:5174"

    @pytest.fixture(autouse=True)
    def _setup_client(self) -> None:
        app = _build_test_app(cors_origins=self._ALLOWED_ORIGIN)
        self._client = TestClient(app, raise_server_exceptions=True)

    def test_401_carries_access_control_allow_origin(self) -> None:
        """GET /api/v1/connectors without auth returns 401 with ACAO for allowed origin."""
        resp = self._client.get(
            "/api/v1/connectors",
            headers={"Origin": self._ALLOWED_ORIGIN},
        )
        assert resp.status_code == 401
        assert resp.headers.get("access-control-allow-origin") == self._ALLOWED_ORIGIN, (
            "401 response must carry Access-Control-Allow-Origin for allowed origin "
            "(CORSMiddleware must be outermost per SPEC-SEC-CORS-001 REQ-6.4)"
        )

    def test_401_carries_vary_origin(self) -> None:
        """The Vary: Origin header is present on the 401 response."""
        resp = self._client.get(
            "/api/v1/connectors",
            headers={"Origin": self._ALLOWED_ORIGIN},
        )
        assert resp.status_code == 401
        vary = resp.headers.get("vary", "")
        assert "Origin" in vary, "Vary: Origin must be present on cross-origin 401 responses"


class TestCorsBlocksEvilOrigin:
    """AC-15 negative: unlisted origin must NOT receive Access-Control-Allow-Origin."""

    _ALLOWED_ORIGIN = "http://localhost:5174"
    _EVIL_ORIGIN = "https://evil.example"

    @pytest.fixture(autouse=True)
    def _setup_client(self) -> None:
        app = _build_test_app(cors_origins=self._ALLOWED_ORIGIN)
        self._client = TestClient(app, raise_server_exceptions=True)

    def test_401_does_not_carry_acao_for_evil_origin(self) -> None:
        """401 from an unlisted origin must NOT echo Access-Control-Allow-Origin."""
        resp = self._client.get(
            "/api/v1/connectors",
            headers={"Origin": self._EVIL_ORIGIN},
        )
        assert resp.status_code == 401
        acao = resp.headers.get("access-control-allow-origin")
        assert acao != self._EVIL_ORIGIN, (
            "401 must NOT echo Access-Control-Allow-Origin for an unlisted origin"
        )
        assert acao != "*", "401 must NOT echo wildcard ACAO for an unlisted origin"
