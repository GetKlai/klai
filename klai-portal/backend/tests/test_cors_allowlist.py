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

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
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

    assert acao != "https://evil.example", (
        "ACAO must NOT be echoed for https://evil.example (AC-1)"
    )
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

    assert acao == "https://my.getklai.com", (
        f"ACAO must echo https://my.getklai.com, got {acao!r} (AC-3)"
    )
    assert acac.lower() == "true", (
        f"ACAC must be true for first-party origin, got {acac!r} (AC-3)"
    )
    assert "origin" in vary.lower(), (
        f"Vary must include Origin, got {vary!r} (AC-3)"
    )


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
    assert acao == "https://acme.getklai.com", (
        f"ACAO must echo acme.getklai.com, got {acao!r} (AC-4a)"
    )


def test_cors_rejects_multi_label_subdomain(cors_client: TestClient) -> None:
    """AC-4b: Origin: https://evil.my.getklai.com must NOT be echoed.

    REQ-1.2 — multi-label subdomains blocked by the fixed regex.
    """
    resp = cors_client.get(
        "/api/me",
        headers={"Origin": "https://evil.my.getklai.com"},
    )

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.my.getklai.com", (
        "ACAO must NOT echo evil.my.getklai.com (multi-label) (AC-4b)"
    )
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
    assert acao != "http://getklai.com", (
        f"ACAO must NOT echo plaintext http://getklai.com, got {acao!r} (AC-5)"
    )
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

    assert acao == "http://localhost:5174", (
        f"ACAO must echo http://localhost:5174, got {acao!r} (AC-6)"
    )
    assert acac.lower() == "true", (
        f"ACAC must be true for localhost dev origin, got {acac!r} (AC-6)"
    )


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
def test_cors_no_unlisted_origin_echo(
    cors_client: TestClient, path: str, origin: str
) -> None:
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
    assert acao != origin, (
        f"ACAO must NOT echo {origin!r} on {path!r} (AC-7)"
    )
    assert acao != "*", (
        f"ACAO must NOT be wildcard for {origin!r} on {path!r} (AC-7)"
    )


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
                f"ACAC=true on {path!r} with origin={origin!r} "
                f"but ACAO={acao!r} - must be a concrete origin (AC-8)"
            )


# ---------------------------------------------------------------------------
# AC-13: Observability — rejected preflights are logged
# ---------------------------------------------------------------------------


def test_cors_rejected_preflight_emits_structlog_event(
    cors_client: TestClient,
) -> None:
    """AC-13: A rejected preflight emits event='cors_origin_rejected' in structlog.

    REQ-1 NFR Observability — event includes origin, path, request_id, kind.
    """
    import structlog as sl

    captured_events: list[dict[str, Any]] = []

    def _capture_processor(
        logger: Any,
        method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        captured_events.append(dict(event_dict))
        raise sl.DropEvent()

    sl.configure(
        processors=[_capture_processor],
        wrapper_class=sl.BoundLogger,
        context_class=dict,
        logger_factory=sl.PrintLoggerFactory(),
    )
    try:
        resp = cors_client.options(
            "/api/me",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
                "X-Request-ID": "test-request-id-001",
            },
        )
    finally:
        sl.reset_defaults()

    acao = resp.headers.get("access-control-allow-origin", "")
    assert acao != "https://evil.example", "Preflight should be rejected (AC-13 setup)"

    rejected = [e for e in captured_events if e.get("event") == "cors_origin_rejected"]
    assert len(rejected) >= 1, (
        f"Expected event='cors_origin_rejected' in structlog, "
        f"got events: {[e.get('event') for e in captured_events]}"
    )

    evt = rejected[0]
    assert evt.get("origin") == "https://evil.example", (
        f"origin field must be 'https://evil.example', got {evt.get('origin')!r}"
    )
    assert evt.get("path") == "/api/me", (
        f"path field must be '/api/me', got {evt.get('path')!r}"
    )
    assert "request_id" in evt, "request_id field must be present in event"
    assert evt.get("kind") == "preflight", (
        f"kind field must be 'preflight' for OPTIONS request, got {evt.get('kind')!r}"
    )


def test_cors_rejected_simple_request_emits_structlog_event(
    cors_client: TestClient,
) -> None:
    """AC-13 (simple-request branch): a non-preflight cross-origin request from a
    rejected origin also emits cors_origin_rejected with kind='simple'.

    Closes the observability gap that the original from-scratch implementation had:
    only preflights were logged. Browsers can issue simple cross-origin GET/POST
    without preflights, and we want both probing channels visible to monitoring.
    """
    import structlog as sl

    captured_events: list[dict[str, Any]] = []

    def _capture_processor(
        logger: Any, method: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        captured_events.append(dict(event_dict))
        raise sl.DropEvent()

    sl.configure(
        processors=[_capture_processor],
        wrapper_class=sl.BoundLogger,
        context_class=dict,
        logger_factory=sl.PrintLoggerFactory(),
    )
    try:
        cors_client.get(
            "/api/me",
            headers={
                "Origin": "https://evil.example",
                "X-Request-ID": "test-request-id-simple-001",
            },
        )
    finally:
        sl.reset_defaults()

    rejected = [
        e
        for e in captured_events
        if e.get("event") == "cors_origin_rejected" and e.get("kind") == "simple"
    ]
    assert len(rejected) >= 1, (
        f"Expected event='cors_origin_rejected' kind='simple' in structlog, "
        f"got events: {[(e.get('event'), e.get('kind')) for e in captured_events]}"
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
