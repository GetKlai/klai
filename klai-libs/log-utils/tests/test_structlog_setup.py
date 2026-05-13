"""Tests for log_utils.structlog_setup -- SPEC-LOGGING-EXTRACT-001.

Covers:

- ``setup_logging`` configures structlog and adds the healthcheck
  filter to ``uvicorn.access``.
- ``HealthCheckAccessFilter`` drops /health and passes everything else
  (including defensive shape checks against future uvicorn changes).
- ``RequestContextMiddleware`` binds ``request_id`` to structlog
  contextvars from the ``X-Request-ID`` header (Caddy-set), generates a
  fresh UUID4 when the header is absent, echoes the id back to the
  client, and binds optional ``org_id`` / ``user_id``.
"""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from log_utils import (
    DEFAULT_HEALTHCHECK_PATHS,
    HealthCheckAccessFilter,
    RequestContextMiddleware,
    setup_logging,
)
from log_utils.structlog_setup import _HealthCheckAccessFilter

# ---------------------------------------------------------------------------
# HealthCheckAccessFilter
# ---------------------------------------------------------------------------


def _make_access_record(method: str, path: str, status: int) -> logging.LogRecord:
    """Build a LogRecord shaped like uvicorn.access emits.

    args layout: (client_addr, method, full_path, http_version, status_code).
    """
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", method, path, "1.1", status),
        exc_info=None,
    )


class TestHealthCheckAccessFilter:
    def setup_method(self) -> None:
        self.filter = HealthCheckAccessFilter()

    def test_health_check_access_filter_drops_health_logs(self) -> None:
        """The headline contract: /health access lines are dropped."""
        record = _make_access_record("GET", "/health", 200)
        assert self.filter.filter(record) is False

    def test_health_with_query_dropped(self) -> None:
        record = _make_access_record("GET", "/health?probe=1", 200)
        assert self.filter.filter(record) is False

    def test_real_request_passes(self) -> None:
        record = _make_access_record("POST", "/notify", 500)
        assert self.filter.filter(record) is True

    def test_unknown_path_passes(self) -> None:
        record = _make_access_record("GET", "/wp-admin", 404)
        assert self.filter.filter(record) is True

    def test_health_prefix_paths_pass(self) -> None:
        """Filter is byte-strict: /healthcheck and /health/sub must pass."""
        for path in ("/healthcheck", "/health/sub", "/healthx"):
            record = _make_access_record("GET", path, 200)
            assert self.filter.filter(record) is True, f"path {path!r} dropped"

    def test_no_args_passes(self) -> None:
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="x",
            args=None,
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_short_args_passes(self) -> None:
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="x %s",
            args=("only-one",),
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_non_string_path_passes(self) -> None:
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1", "GET", 12345, "1.1", 200),
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_custom_paths(self) -> None:
        f = HealthCheckAccessFilter(paths={"/livez", "/readyz"})
        for path in ("/livez", "/readyz"):
            assert f.filter(_make_access_record("GET", path, 200)) is False
        assert f.filter(_make_access_record("GET", "/health", 200)) is True

    def test_private_alias_is_same_class(self) -> None:
        """Backward-compat: code importing ``_HealthCheckAccessFilter`` keeps working."""
        assert _HealthCheckAccessFilter is HealthCheckAccessFilter


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore root + uvicorn.access state around each test.

    setup_logging mutates global logging state. Without this fixture, one
    test's setup_logging call bleeds into subsequent tests.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    access = logging.getLogger("uvicorn.access")
    saved_access_filters = list(access.filters)
    saved_access_level = access.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    access.filters = saved_access_filters
    access.setLevel(saved_access_level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


class TestSetupLogging:
    def test_setup_logging_configures_structlog(self) -> None:
        setup_logging("test-svc")
        # is_configured() is the structlog API for "configure() was called"
        assert structlog.is_configured() is True

    def test_setup_logging_binds_service_contextvar(self) -> None:
        setup_logging("test-svc")
        merged = structlog.contextvars.get_contextvars()
        assert merged.get("service") == "test-svc"

    def test_setup_logging_routes_uvicorn_access_at_info(self) -> None:
        setup_logging("test-svc")
        access = logging.getLogger("uvicorn.access")
        assert access.level == logging.INFO

    def test_setup_logging_attaches_healthcheck_filter(self) -> None:
        setup_logging("test-svc")
        access = logging.getLogger("uvicorn.access")
        # At least one of the attached filters is our HealthCheckAccessFilter
        attached = [f for f in access.filters if isinstance(f, HealthCheckAccessFilter)]
        assert len(attached) == 1

    def test_setup_logging_silences_default_third_party(self) -> None:
        setup_logging("test-svc")
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_setup_logging_extra_third_party_levels_merge(self) -> None:
        setup_logging("test-svc", third_party_levels={"redis": logging.WARNING})
        assert logging.getLogger("redis").level == logging.WARNING
        # Defaults still applied
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_setup_logging_custom_healthcheck_paths(self) -> None:
        setup_logging("test-svc", healthcheck_paths={"/livez"})
        access = logging.getLogger("uvicorn.access")
        attached = [f for f in access.filters if isinstance(f, HealthCheckAccessFilter)]
        assert len(attached) == 1
        # Filter's paths reflect the override
        assert attached[0].filter(_make_access_record("GET", "/livez", 200)) is False
        assert attached[0].filter(_make_access_record("GET", "/health", 200)) is True

    def test_default_healthcheck_paths_is_health(self) -> None:
        assert DEFAULT_HEALTHCHECK_PATHS == frozenset({"/health"})


# ---------------------------------------------------------------------------
# RequestContextMiddleware
# ---------------------------------------------------------------------------


def _build_app(*, service_name: str | None = None, bind_user_id: bool = False) -> Starlette:
    """Build a minimal Starlette app with the middleware installed.

    The ``/echo`` endpoint returns the merged structlog contextvars so
    tests can assert on what got bound during the request.
    """

    async def echo(request: Request) -> JSONResponse:
        return JSONResponse(dict(structlog.contextvars.get_contextvars()))

    app = Starlette(routes=[Route("/echo", echo)])
    app.add_middleware(
        RequestContextMiddleware,
        service_name=service_name,
        bind_user_id=bind_user_id,
    )
    return app


@pytest.mark.asyncio
async def test_request_context_middleware_binds_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no header is set, middleware generates a deterministic UUID4."""
    fake_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(
        "log_utils.structlog_setup.uuid.uuid4",
        lambda: fake_uuid,
    )
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == str(fake_uuid)
    # And the response echoes it back to the client
    assert response.headers["x-request-id"] == str(fake_uuid)


@pytest.mark.asyncio
async def test_request_context_middleware_uses_caddy_header() -> None:
    """When Caddy passes X-Request-ID, middleware honours it (does NOT regenerate)."""
    caddy_id = "caddy-trace-abc-123"
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-Request-ID": caddy_id})
    body = response.json()
    assert body["request_id"] == caddy_id
    assert response.headers["x-request-id"] == caddy_id


@pytest.mark.asyncio
async def test_request_context_middleware_binds_org_id() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-Org-ID": "org-42"})
    body = response.json()
    assert body["org_id"] == "org-42"


@pytest.mark.asyncio
async def test_request_context_middleware_org_id_absent_when_header_missing() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo")
    body = response.json()
    assert "org_id" not in body


@pytest.mark.asyncio
async def test_request_context_middleware_binds_service_when_configured() -> None:
    app = _build_app(service_name="klai-test-svc")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo")
    body = response.json()
    assert body["service"] == "klai-test-svc"


@pytest.mark.asyncio
async def test_request_context_middleware_user_id_optional_off() -> None:
    """user_id is opt-in — even when X-User-ID is set it's not bound by default."""
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-User-ID": "u-99"})
    body = response.json()
    assert "user_id" not in body


@pytest.mark.asyncio
async def test_request_context_middleware_user_id_bound_when_enabled() -> None:
    app = _build_app(bind_user_id=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-User-ID": "u-99"})
    body = response.json()
    assert body["user_id"] == "u-99"
