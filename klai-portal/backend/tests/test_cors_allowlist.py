"""CORS allowlist regression tests — SPEC-SEC-CORS-001.

Tests AC-1 through AC-8, AC-13, and AC-14.

Strategy: build a minimal FastAPI app that registers KlaiCORSMiddleware (the
production middleware) and a small set of routes, then run CORS scenarios
against it via Starlette TestClient.  This avoids importing the full portal-api
app (which requires live DB connections and secret validation).

Fixtures are module-scoped: a single FastAPI + TestClient pair handles every
in-spec scenario via header probing, which is read-only and side-effect-free.
The two outliers (AC-13 reconfigures structlog; AC-14 monkeypatches the regex
constant) use pytest's `monkeypatch` for idempotent cleanup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Test app factory + module-scoped fixtures
# ---------------------------------------------------------------------------


def _make_test_app(cors_origins: str = "http://localhost:5174") -> FastAPI:
    """Create a minimal app with KlaiCORSMiddleware for CORS testing.

    `cors_origins` is the raw comma-separated string (equivalent to
    settings.cors_origins); we split it once here, the same way
    Settings.cors_origins_list does in production code.
    """
    from app.middleware.klai_cors import KlaiCORSMiddleware

    app = FastAPI()

    @app.get("/api/me")
    async def me() -> JSONResponse:
        return JSONResponse({"user": "test"})

    @app.post("/api/auth/login")
    async def login() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/api/signup")
    async def signup() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/internal/anything")
    async def internal() -> JSONResponse:
        return JSONResponse({"ok": True})

    # Public widget endpoints, mirrored from app/api/partner.py IN ITS
    # PRODUCTION SHAPE: an APIRouter with prefix="/partner/v1", registered
    # with app.include_router(). The widget bundle calls these from the
    # customer's own site; they carry the same marker the production routes
    # carry, and /widget-config has a widget-aware preflight route that
    # echoes the origin when the widget's allowlist matches. Registering
    # them any other way hides the include_router wrapper bug the marker
    # walk had in FastAPI 0.141+ (see _collect_widget_cors_entries).
    from app.api.widget_public import WIDGET_PUBLIC_CORS, WIDGET_ROUTE_DECIDES_CORS

    widget_router = APIRouter(prefix="/partner/v1")

    @widget_router.post("/chat/completions", openapi_extra=WIDGET_PUBLIC_CORS)
    async def widget_chat() -> JSONResponse:
        return JSONResponse({"ok": True})

    @widget_router.post("/widget/feedback", openapi_extra=WIDGET_PUBLIC_CORS)
    async def widget_feedback() -> JSONResponse:
        return JSONResponse({"ok": True})

    @widget_router.options("/widget-config", openapi_extra=WIDGET_ROUTE_DECIDES_CORS)
    async def widget_config_preflight(id: str) -> JSONResponse:
        headers = {"X-Widget-Route": id}
        if id == "wgt_allows_customer":
            headers["Access-Control-Allow-Origin"] = "https://help.customer.example"
        return JSONResponse(None, status_code=204, headers=headers)

    @widget_router.get("/widget-config", openapi_extra=WIDGET_ROUTE_DECIDES_CORS)
    async def widget_config(id: str) -> JSONResponse:
        if id == "wgt_allows_customer":
            return JSONResponse({"ok": True}, headers={"Access-Control-Allow-Origin": "https://help.customer.example"})
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)

    @widget_router.get("/widget-handoffs/hubspot/events", openapi_extra=WIDGET_PUBLIC_CORS)
    async def widget_handoff_events() -> StreamingResponse:
        async def events() -> AsyncIterator[bytes]:
            yield b"id: 1\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    app.include_router(widget_router)

    app.add_middleware(
        KlaiCORSMiddleware,
        cors_origins=[o.strip() for o in cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


@pytest.fixture(scope="module")
def cors_client() -> TestClient:
    """Module-scoped test client with default cors_origins=localhost:5174.

    Reused across the 24 tests that exercise the default config. The two
    structlog/regex tests use pytest's monkeypatch and do not need a fresh
    app since they probe behaviour, not state.
    """
    return TestClient(_make_test_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC-1: Cross-origin GET /api/me from evil.example is blocked
# ---------------------------------------------------------------------------


def test_cors_blocks_evil_origin_on_api_me(cors_client: TestClient) -> None:
    """AC-1: GET /api/me with Origin: evil.example must NOT echo ACAO or ACAC.

    REQ-1.1 / REQ-1.5 — wildcard CORS regex removed; evil.example not in allowlist.
    """
    resp = cors_client.options(
        "/api/me",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    acac = resp.headers.get("access-control-allow-credentials", "")

    assert acao != "https://evil.example", "ACAO must NOT be echoed for https://evil.example (AC-1)"
    assert acao != "*", "ACAO must NOT be wildcard (AC-1)"
    assert acac.lower() != "true", "ACAC must NOT be true for evil.example (AC-1)"


# ---------------------------------------------------------------------------
# AC-2: Cross-origin POST /api/auth/login preflight from evil.example is blocked
# ---------------------------------------------------------------------------


def test_cors_blocks_evil_origin_on_auth_login_preflight(
    cors_client: TestClient,
) -> None:
    """AC-2: OPTIONS /api/auth/login with Origin: evil.example must NOT echo ACAO.

    REQ-1.1 — the CSRF exemption does NOT override the CORS gate.
    """
    resp = cors_client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.example", (
        "ACAO must NOT be echoed for evil.example on /api/auth/login preflight (AC-2)"
    )
    assert acao != "*", "ACAO must NOT be wildcard on /api/auth/login preflight (AC-2)"


# ---------------------------------------------------------------------------
# AC-3: First-party GET /api/me from my.getklai.com is allowed with credentials
# ---------------------------------------------------------------------------


def test_cors_allows_first_party_on_api_me(cors_client: TestClient) -> None:
    """AC-3: GET /api/me with Origin: https://my.getklai.com echoes ACAO + ACAC.

    REQ-1.2 / REQ-1.5 — fixed regex matches *.getklai.com.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "https://my.getklai.com"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    acac = resp.headers.get("access-control-allow-credentials", "")
    vary = resp.headers.get("vary", "")

    assert acao == "https://my.getklai.com", f"ACAO must echo https://my.getklai.com, got {acao!r} (AC-3)"
    assert acac.lower() == "true", f"ACAC must be true for first-party origin, got {acac!r} (AC-3)"
    assert "origin" in vary.lower(), f"Vary must include Origin, got {vary!r} (AC-3)"


# ---------------------------------------------------------------------------
# AC-4: First-party tenant subdomain allowed; multi-label rejected
# ---------------------------------------------------------------------------


def test_cors_allows_tenant_subdomain_on_api_me(cors_client: TestClient) -> None:
    """AC-4a: Origin: https://acme.getklai.com is allowed.

    REQ-1.2 — single-label subdomain matches the fixed regex.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "https://acme.getklai.com"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao == "https://acme.getklai.com", f"ACAO must echo acme.getklai.com, got {acao!r} (AC-4a)"


def test_cors_rejects_multi_label_subdomain(cors_client: TestClient) -> None:
    """AC-4b: Origin: https://evil.my.getklai.com must NOT be echoed.

    REQ-1.2 — multi-label subdomains blocked by the fixed regex.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "https://evil.my.getklai.com"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.my.getklai.com", "ACAO must NOT echo evil.my.getklai.com (multi-label) (AC-4b)"
    assert acao != "*", "ACAO must NOT be wildcard (AC-4b)"


# ---------------------------------------------------------------------------
# AC-5: Plaintext http://getklai.com is rejected
# ---------------------------------------------------------------------------


def test_cors_rejects_plaintext_http_getklai(cors_client: TestClient) -> None:
    """AC-5: Origin: http://getklai.com (no TLS) is rejected.

    REQ-1.2 — regex requires https://.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "http://getklai.com"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "http://getklai.com", f"ACAO must NOT echo plaintext http://getklai.com, got {acao!r} (AC-5)"
    assert acao != "*", "ACAO must NOT be wildcard (AC-5)"


# ---------------------------------------------------------------------------
# AC-6: Dev origin http://localhost:5174 is allowed when configured
# ---------------------------------------------------------------------------


def test_cors_allows_dev_origin_localhost_5174(cors_client: TestClient) -> None:
    """AC-6: Origin: http://localhost:5174 is allowed when in cors_origins.

    REQ-1.2 — cors_origins_list union with the fixed regex.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "http://localhost:5174"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    acac = resp.headers.get("access-control-allow-credentials", "")

    assert acao == "http://localhost:5174", f"ACAO must echo http://localhost:5174, got {acao!r} (AC-6)"
    assert acac.lower() == "true", f"ACAC must be true for localhost dev origin, got {acac!r} (AC-6)"


# ---------------------------------------------------------------------------
# AC-7: No unlisted origin echoed on any path (table test)
# ---------------------------------------------------------------------------

_ATTACKER_ORIGINS = [
    "https://evil.example",
    "https://evil.getklai.com.attacker.tld",
    "http://getklai.com",
    "https://evil.my.getklai.com",
]

_TEST_PATHS = [
    "/api/me",
    "/api/auth/login",
    "/api/signup",
    "/internal/anything",
]


@pytest.mark.parametrize("path", _TEST_PATHS)
@pytest.mark.parametrize("origin", _ATTACKER_ORIGINS)
def test_cors_no_unlisted_origin_echo(cors_client: TestClient, path: str, origin: str) -> None:
    """AC-7: Preflights from attacker origins never echo ACAO on any path.

    REQ-1 (group) — no attacker origin x path combination echoes ACAO.
    """
    resp = cors_client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != origin, f"ACAO must NOT echo {origin!r} on {path!r} (AC-7)"
    assert acao != "*", f"ACAO must NOT be wildcard for {origin!r} on {path!r} (AC-7)"


# ---------------------------------------------------------------------------
# AC-8: ACAC never coexists with wildcard origin
# ---------------------------------------------------------------------------


def test_acac_never_with_wildcard_origin(cors_client: TestClient) -> None:
    """AC-8: When ACAC is true, ACAO must be a concrete origin (not * or missing).

    REQ-1.5 — scans headers from a representative mix of allowed and denied scenarios.
    """
    probes: list[tuple[str, str]] = [
        ("/api/me", "https://my.getklai.com"),
        ("/api/me", "https://acme.getklai.com"),
        ("/api/me", "http://localhost:5174"),
        ("/api/me", "https://evil.example"),
        ("/api/auth/login", "https://my.getklai.com"),
        ("/api/auth/login", "https://evil.example"),
    ]

    for path, origin in probes:
        resp = cors_client.get(path, headers={"Origin": origin})
        acao = resp.headers.get("access-control-allow-origin", "")
        acac = resp.headers.get("access-control-allow-credentials", "")

        if acac.lower() == "true":
            assert acao not in ("", "*"), (
                f"ACAC=true on {path!r} with origin={origin!r} but ACAO={acao!r} - must be a concrete origin (AC-8)"
            )


# ---------------------------------------------------------------------------
# AC-13: Observability — rejected preflights are logged
# ---------------------------------------------------------------------------


def test_cors_rejected_preflight_emits_structlog_event(
    cors_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-13: A rejected preflight emits event='cors_origin_rejected' in structlog.

    REQ-1 NFR Observability — event includes origin, path, request_id, kind.
    """
    from unittest.mock import MagicMock

    # Patch the module-level structlog proxy directly. ``sl.configure`` +
    # ``capture_logs``-style fixtures are unreliable here: ``setup_logging``
    # in ``app.main`` runs ``cache_logger_on_first_use=True``, which means
    # any prior test that imported ``app.api.auth`` (or any other module
    # whose ``_slog`` resolved on first use) locks the proxy chain to the
    # full setup_logging pipeline. A subsequent ``sl.configure`` does not
    # update the cached chain, so events route through the JSON renderer
    # instead of our capture processor and ``captured_events`` stays empty.
    # Patching the proxy bypasses the cache entirely.
    import app.middleware.klai_cors as cors_module

    mock_logger = MagicMock()
    monkeypatch.setattr(cors_module, "logger", mock_logger)

    resp = cors_client.options(
        "/api/me",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "X-Request-ID": "test-request-id-001",
        },
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.example", "Preflight should be rejected (AC-13 setup)"

    rejected = [
        call.kwargs for call in mock_logger.info.call_args_list if call.args and call.args[0] == "cors_origin_rejected"
    ]
    assert len(rejected) >= 1, (
        f"Expected event='cors_origin_rejected' in logger.info calls, got {mock_logger.info.call_args_list!r}"
    )

    evt = rejected[0]
    assert evt.get("origin") == "https://evil.example", (
        f"origin field must be 'https://evil.example', got {evt.get('origin')!r}"
    )
    assert evt.get("path") == "/api/me", f"path field must be '/api/me', got {evt.get('path')!r}"
    assert "request_id" in evt, "request_id field must be present in event"
    assert evt.get("kind") == "preflight", (
        f"kind field must be 'preflight' for OPTIONS request, got {evt.get('kind')!r}"
    )


def test_cors_rejected_simple_request_emits_structlog_event(
    cors_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-13 (simple-request branch): a non-preflight cross-origin request from a
    rejected origin also emits cors_origin_rejected with kind='simple'.

    Closes the observability gap that the original from-scratch implementation had:
    only preflights were logged. Browsers can issue simple cross-origin GET/POST
    without preflights, and we want both probing channels visible to monitoring.
    """
    from unittest.mock import MagicMock

    # Same MagicMock-on-proxy pattern as the preflight test above. See
    # comment there for why ``sl.configure`` + ``capture_logs`` are not
    # reliable in this codebase.
    import app.middleware.klai_cors as cors_module

    mock_logger = MagicMock()
    monkeypatch.setattr(cors_module, "logger", mock_logger)

    cors_client.get(
        "/api/me",
        headers={
            "Origin": "https://evil.example",
            "X-Request-ID": "test-request-id-simple-001",
        },
    )

    rejected = [
        call.kwargs
        for call in mock_logger.info.call_args_list
        if call.args and call.args[0] == "cors_origin_rejected" and call.kwargs.get("kind") == "simple"
    ]
    assert len(rejected) >= 1, (
        f"Expected event='cors_origin_rejected' kind='simple' in logger.info calls, "
        f"got {[(c.args, c.kwargs.get('kind')) for c in mock_logger.info.call_args_list]!r}"
    )

    evt = rejected[0]
    assert evt.get("origin") == "https://evil.example"
    assert evt.get("path") == "/api/me"
    assert evt.get("request_id") == "test-request-id-simple-001"


# ---------------------------------------------------------------------------
# AC-14: Startup fail-closed on broken regex
# ---------------------------------------------------------------------------


def test_cors_regex_compile_failure_raises_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-14: If the CORS origin regex fails to compile, startup raises SystemExit.

    REQ-1 NFR Fail mode — fail-closed like _require_vexa_webhook_secret.
    The regex is hardcoded so we monkeypatch the module constant; pytest's
    monkeypatch fixture handles automatic cleanup.
    """
    import app.middleware.klai_cors as cors_module

    monkeypatch.setattr(cors_module, "_FIRST_PARTY_ORIGIN_PATTERN", "[")

    with pytest.raises(SystemExit):
        cors_module._compile_first_party_regex()


# ---------------------------------------------------------------------------
# Public widget endpoints: a customer's own origin is the normal caller
# ---------------------------------------------------------------------------

CUSTOMER_ORIGIN = "https://help.customer.example"


@pytest.mark.parametrize(
    ("path", "method", "requested_headers"),
    [
        ("/partner/v1/chat/completions", "POST", "content-type,authorization"),
        ("/partner/v1/widget/feedback", "POST", "content-type,authorization"),
        # The SSE client re-sends Last-Event-ID when it reconnects.
        ("/partner/v1/widget-handoffs/hubspot/events", "GET", "authorization,last-event-id"),
    ],
)
def test_widget_preflight_from_customer_origin_is_answered(
    cors_client: TestClient, path: str, method: str, requested_headers: str
) -> None:
    """The widget on a customer's site must get through the preflight: the
    security boundary there is the widget session JWT, not the first-party
    origin allowlist."""
    response = cors_client.options(
        path,
        headers={
            "Origin": CUSTOMER_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": requested_headers,
        },
    )
    assert response.status_code == 204, response.text
    assert response.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
    allowed = response.headers.get("access-control-allow-headers", "").lower()
    assert all(h in allowed for h in requested_headers.split(","))
    assert "access-control-allow-credentials" not in response.headers


def test_widget_chat_response_from_customer_origin_carries_acao(cors_client: TestClient) -> None:
    response = cors_client.post("/partner/v1/chat/completions", headers={"Origin": CUSTOMER_ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


def test_widget_sse_stream_from_customer_origin_carries_acao(cors_client: TestClient) -> None:
    with cors_client.stream(
        "GET", "/partner/v1/widget-handoffs/hubspot/events", headers={"Origin": CUSTOMER_ORIGIN}
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
        assert b"id: 1" in b"".join(response.iter_bytes())


def test_widget_config_preflight_is_decided_by_the_widget_route(cors_client: TestClient) -> None:
    """/widget-config decides per widget allowlist in its own OPTIONS route;
    the global middleware hands the preflight through and must not add an
    origin echo the route deliberately left out."""
    headers = {
        "Origin": CUSTOMER_ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-klai-widget-session-id",
    }
    allowed = cors_client.options("/partner/v1/widget-config?id=wgt_allows_customer", headers=headers)
    assert allowed.status_code == 204, allowed.text
    assert allowed.headers.get("x-widget-route") == "wgt_allows_customer"
    assert allowed.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN

    rejected = cors_client.options("/partner/v1/widget-config?id=wgt_other", headers=headers)
    assert rejected.status_code == 204, rejected.text
    assert rejected.headers.get("x-widget-route") == "wgt_other"
    assert "access-control-allow-origin" not in rejected.headers

    # The GET follows the same rule: a 403 for a disallowed origin stays
    # unreadable cross-origin (AC-10), an allowed origin keeps the route's echo.
    denied = cors_client.get("/partner/v1/widget-config?id=wgt_other", headers={"Origin": CUSTOMER_ORIGIN})
    assert denied.status_code == 403
    assert "access-control-allow-origin" not in denied.headers
    granted = cors_client.get("/partner/v1/widget-config?id=wgt_allows_customer", headers={"Origin": CUSTOMER_ORIGIN})
    assert granted.status_code == 200
    assert granted.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN


def test_widget_path_match_is_exact(cors_client: TestClient) -> None:
    response = cors_client.options(
        "/partner/v1/chat/completions-admin",
        headers={"Origin": CUSTOMER_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_customer_origin_is_still_rejected_outside_widget_paths(cors_client: TestClient) -> None:
    response = cors_client.options(
        "/api/auth/login",
        headers={"Origin": CUSTOMER_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Ownership: the route carries the marker, klai_cors.py knows no paths
# ---------------------------------------------------------------------------


def test_widget_public_marker_teaches_the_middleware_a_new_route() -> None:
    """A NEW route that only carries the widget-public marker gets its
    preflight answered from a customer origin — without klai_cors.py knowing
    anything about its path. A route WITHOUT the marker on the same app still
    gets 400."""
    from app.api.widget_public import WIDGET_PUBLIC_CORS
    from app.middleware.klai_cors import KlaiCORSMiddleware

    app = FastAPI()

    @app.post("/partner/v1/bookings", openapi_extra=WIDGET_PUBLIC_CORS)
    async def booking() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/partner/v1/internal-secret")
    async def internal_secret() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    preflight = client.options(
        "/partner/v1/bookings",
        headers={
            "Origin": CUSTOMER_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 204, preflight.text
    assert preflight.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
    assert "access-control-allow-credentials" not in preflight.headers

    denied = client.options(
        "/partner/v1/internal-secret",
        headers={"Origin": CUSTOMER_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def _preflight(client: TestClient, path: str, method: str) -> httpx.Response:
    return client.options(
        path,
        headers={
            "Origin": CUSTOMER_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_widget_routes_registered_with_include_router_are_answered() -> None:
    """Regression (review finding 1): production registers the partner
    routes with ``app.include_router(partner_router)``, which on FastAPI
    0.141+ puts an ``_IncludedRouter`` (without ``openapi_extra``) in
    ``app.routes``. A flat top-level scan saw no marked route and 400'd
    every widget preflight from a customer domain. Build the app exactly
    like production — APIRouter with prefix, included, middleware added
    last — and the preflight must get 204 with the origin echoed."""
    from app.api.widget_public import WIDGET_PUBLIC_CORS
    from app.middleware.klai_cors import KlaiCORSMiddleware

    router = APIRouter(prefix="/partner/v1")

    @router.post("/chat/completions", openapi_extra=WIDGET_PUBLIC_CORS)
    async def chat() -> JSONResponse:
        return JSONResponse({"ok": True})

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    preflight = _preflight(client, "/partner/v1/chat/completions", "POST")
    assert preflight.status_code == 204, preflight.text
    assert preflight.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
    assert "access-control-allow-credentials" not in preflight.headers


def test_widget_routes_under_a_mount_are_answered() -> None:
    """Same bug class as the include_router regression: a marked route
    registered under ``app.mount()`` is invisible to a flat scan of
    app.routes, and Starlette Mounts rewrite the path before their inner
    routes match. It must still echo."""
    from app.api.widget_public import WIDGET_PUBLIC_CORS
    from app.middleware.klai_cors import KlaiCORSMiddleware

    inner = APIRouter()

    @inner.post("/widget/feedback", openapi_extra=WIDGET_PUBLIC_CORS)
    async def feedback() -> JSONResponse:
        return JSONResponse({"ok": True})

    app = FastAPI()
    app.mount("/outer", inner)
    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    preflight = _preflight(client, "/outer/widget/feedback", "POST")
    assert preflight.status_code == 204, preflight.text
    assert preflight.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN


def test_widget_routes_under_a_mount_inside_an_include_router_are_answered() -> None:
    """Both wrappers at once: include_router wraps the Mount in a FastAPI
    effective context, and the mount itself rewrites the path again before
    its inner routes match (a Mount skips its router's own prefix, so the
    router-resolvable path here is /m/widget/feedback). The walk must follow
    the context to the mounted app's routes and replay both rewrites."""
    from app.api.widget_public import WIDGET_PUBLIC_CORS
    from app.middleware.klai_cors import KlaiCORSMiddleware

    inner = APIRouter()

    @inner.post("/widget/feedback", openapi_extra=WIDGET_PUBLIC_CORS)
    async def feedback() -> JSONResponse:
        return JSONResponse({"ok": True})

    outer = APIRouter()
    outer.mount("/m", inner)

    app = FastAPI()
    app.include_router(outer)
    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    preflight = _preflight(client, "/m/widget/feedback", "POST")
    assert preflight.status_code == 204, preflight.text
    assert preflight.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN


def test_unmarked_full_match_wins_over_marked_partial_on_the_same_path() -> None:
    """Regression (review finding 2): Starlette keeps looking for a FULL
    match after a PARTIAL one. A marked GET registered BEFORE an unmarked
    POST on the same path must not make the POST inherit the widget echo:
    the preflight is decided with Access-Control-Request-Method as the
    method, so a POST preflight resolves to the POST route (unmarked ->
    first-party policy -> 400 from a customer origin) and only a GET
    preflight resolves to the marked GET."""
    from app.api.widget_public import WIDGET_PUBLIC_CORS
    from app.middleware.klai_cors import KlaiCORSMiddleware

    app = FastAPI()

    @app.get("/partner/v1/bookings", openapi_extra=WIDGET_PUBLIC_CORS)
    async def list_bookings() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/partner/v1/bookings")
    async def create_booking() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    post_preflight = _preflight(client, "/partner/v1/bookings", "POST")
    assert post_preflight.status_code == 400, post_preflight.text
    assert "access-control-allow-origin" not in post_preflight.headers

    actual_post = client.post("/partner/v1/bookings", headers={"Origin": CUSTOMER_ORIGIN})
    assert actual_post.status_code == 200
    assert "access-control-allow-origin" not in actual_post.headers

    get_preflight = _preflight(client, "/partner/v1/bookings", "GET")
    assert get_preflight.status_code == 204, get_preflight.text
    assert get_preflight.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN


@pytest.mark.parametrize(
    ("path", "method", "expected_status"),
    [
        ("/partner/v1/chat/completions", "POST", 204),
        ("/partner/v1/widget/feedback", "POST", 204),
        ("/partner/v1/widget-handoffs/hubspot/start", "POST", 204),
        ("/partner/v1/widget-handoffs/hubspot/messages", "POST", 204),
        ("/partner/v1/widget-handoffs/hubspot/events", "GET", 204),
        # Unmarked sibling on the same production router: must NOT inherit.
        ("/partner/v1/responses", "POST", 400),
    ],
)
def test_production_partner_router_answers_widget_preflights(path: str, method: str, expected_status: int) -> None:
    """End-to-end on the real app.api.partner router, registered exactly as
    main.py does (APIRouter with prefix, include_router, middleware added
    last). Preflights are answered by the middleware and never reach the
    endpoints, so no DB is needed. This is the shape the fixture-level tests
    could not cover while they registered routes directly on FastAPI."""
    from app.api.partner import router as partner_router
    from app.middleware.klai_cors import KlaiCORSMiddleware

    app = FastAPI()
    app.include_router(partner_router)
    app.add_middleware(KlaiCORSMiddleware, cors_origins=[])
    client = TestClient(app, raise_server_exceptions=False)

    response = _preflight(client, path, method)
    assert response.status_code == expected_status, response.text
    if expected_status == 204:
        assert response.headers.get("access-control-allow-origin") == CUSTOMER_ORIGIN
        assert "access-control-allow-credentials" not in response.headers
    else:
        assert "access-control-allow-origin" not in response.headers


def test_production_widget_routes_carry_their_marker() -> None:
    """Review finding 4: the widget bundle's production endpoints must all
    declare their CORS mode, or a customer-domain widget breaks silently
    (help.voys.nl, PR #1361). Import app.api.partner directly — no DB, no
    app — and assert the marker inventory on every route the bundle calls.
    An unmarked new widget endpoint fails here; marking a route nobody
    calls fails here too (equality, not subset)."""
    from app.api.partner import router as partner_router
    from app.api.widget_public import MODE_ECHO, MODE_ROUTE_DECIDES, widget_cors_mode_of

    expected = {
        ("POST", "/partner/v1/chat/completions"): MODE_ECHO,
        ("POST", "/partner/v1/widget/feedback"): MODE_ECHO,
        ("POST", "/partner/v1/widget-handoffs/hubspot/start"): MODE_ECHO,
        ("POST", "/partner/v1/widget-handoffs/hubspot/messages"): MODE_ECHO,
        ("GET", "/partner/v1/widget-handoffs/hubspot/events"): MODE_ECHO,
        ("GET", "/partner/v1/widget-config"): MODE_ROUTE_DECIDES,
        ("OPTIONS", "/partner/v1/widget-config"): MODE_ROUTE_DECIDES,
    }

    declared: dict[tuple[str, str], str] = {}
    for route in partner_router.routes:
        mode = widget_cors_mode_of(route)
        if mode is None:
            continue
        methods = {str(m) for m in getattr(route, "methods", set())} - {"HEAD"}
        for method in methods:
            declared[(method, str(route.path))] = mode

    assert declared == expected
