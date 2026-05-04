"""Tests for KlaiTenantHostMiddleware (tenant-host guard).

Verifies that requests where the URL hostname's tenant slug does not match
the session's org slug are rejected with the appropriate response shape:
302 redirect for HTML navigation, 409 JSON for XHR. Skip rules cover
unauthenticated requests, OPTIONS preflights, exempt path prefixes, and
non-tenant subdomains (e.g. ``my.getklai.com``).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.session import SessionContext
from app.middleware import tenant_host as mw_module
from app.middleware.tenant_host import KlaiTenantHostMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    host: str = "voys.getklai.com",
    path: str = "/api/me",
    method: str = "GET",
    query: str = "",
    accept: str = "application/json",
    session_org_id: int | None = 42,
) -> MagicMock:
    """Build a Starlette-compatible request mock."""
    request = MagicMock()
    request.method = method
    request.url = MagicMock()
    request.url.hostname = host
    request.url.path = path
    request.url.query = query
    request.headers = {"accept": accept}

    if session_org_id is not None:
        request.state.session = SessionContext(
            sid="test-sid",
            zitadel_user_id="test-user",
            access_token="test-token",
            csrf_token="test-csrf",
            access_token_expires_at=2_000_000_000,
            org_id=session_org_id,
        )
    else:
        request.state.session = None

    return request


def _slug_resolver(mapping: dict[int, str]) -> Any:
    """Return an AsyncMock that maps org_id → slug."""
    return AsyncMock(side_effect=lambda org_id: mapping.get(org_id))


@pytest.fixture(autouse=True)
def _clear_slug_cache() -> None:
    """Each test starts with an empty slug cache."""
    mw_module._slug_cache_clear()


# ---------------------------------------------------------------------------
# Pass-through cases
# ---------------------------------------------------------------------------


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_matching_slug_passes(self) -> None:
        """Host slug matches session org slug — request proceeds."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host="voys.getklai.com", session_org_id=42)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "voys"})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_session_passes(self) -> None:
        """Unauthenticated requests are someone else's problem."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(session_org_id=None)
        call_next = AsyncMock(return_value=MagicMock(status_code=401))

        response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_options_preflight_passes(self) -> None:
        """CORS preflight must reach the CORS middleware unmolested."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(method="OPTIONS")
        call_next = AsyncMock(return_value=MagicMock(status_code=204))

        response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 204

    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/oidc/start",
            "/api/auth/idp-callback",
            "/api/signup",
            "/api/health",
            "/api/perf",
            "/api/public/anything",
            "/api/webhooks/moneybird",
            "/internal/something",
            "/partner/v1/widget-config",
            "/health",
            "/docs",
            "/openapi.json",
        ],
    )
    @pytest.mark.asyncio
    async def test_skip_path_prefixes(self, path: str) -> None:
        """Pre-auth, internal, partner, and probe paths are exempt."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host="wrong.getklai.com", path=path, session_org_id=42)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "voys"})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "host",
        [
            "my.getklai.com",
            "auth.getklai.com",
            "llm.getklai.com",
            "grafana.getklai.com",
            "errors.getklai.com",
            "connector.getklai.com",
            "logs-ingest.getklai.com",
            "dev.getklai.com",
        ],
    )
    @pytest.mark.asyncio
    async def test_non_tenant_subdomains_pass(self, host: str) -> None:
        """Klai infrastructure subdomains are not tenant slugs."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host=host, session_org_id=42)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "127.0.0.1",
            "portal-api",
            "example.com",  # custom domain not under getklai.com
        ],
    )
    @pytest.mark.asyncio
    async def test_non_klai_hosts_pass(self, host: str) -> None:
        """Hosts outside ``*.getklai.com`` are skipped (dev / custom domains)."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host=host, session_org_id=42)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_org_slug_fails_open(self) -> None:
        """If session.org_id has no portal_orgs row, fail open."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host="voys.getklai.com", session_org_id=999)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "bad_slug",
        [
            "evil.com",  # would escape the .getklai.com suffix
            "bad/path",  # path separator
            "with space",  # whitespace
            "UPPERCASE",  # uppercase forbidden
            "-leadinghyphen",
            "trailing-",
            "",  # empty
            "a" * 65,  # exceeds Klai MAX_SLUG_LENGTH (64)
        ],
    )
    @pytest.mark.asyncio
    async def test_invalid_session_slug_fails_open(self, bad_slug: str) -> None:
        """A session slug that does not match the hostname-label regex is
        rejected (fail-open, not 302/409) — defense-in-depth against any
        future schema bug producing a malformed slug."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(host="voys.getklai.com", session_org_id=42)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: bad_slug})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Mismatch cases
# ---------------------------------------------------------------------------


class TestMismatchResponse:
    @pytest.mark.asyncio
    async def test_html_request_returns_302_redirect(self) -> None:
        """Browser navigation gets a 302 to the correct subdomain."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(
            host="voys.getklai.com",
            path="/admin/users",
            session_org_id=42,
            accept="text/html,application/xhtml+xml",
        )
        call_next = AsyncMock()

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "getklai"})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_not_awaited()
        assert response.status_code == 302
        assert response.headers["location"] == "https://getklai.getklai.com/admin/users"

    @pytest.mark.asyncio
    async def test_html_request_preserves_query_string(self) -> None:
        """The ?foo=bar tail follows the redirect."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(
            host="voys.getklai.com",
            path="/admin/users",
            query="page=2&filter=admin",
            session_org_id=42,
            accept="text/html",
        )
        call_next = AsyncMock()

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "getklai"})):
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 302
        assert response.headers["location"] == ("https://getklai.getklai.com/admin/users?page=2&filter=admin")

    @pytest.mark.asyncio
    async def test_xhr_request_returns_409_json(self) -> None:
        """XHR/fetch gets a 409 with structured error_code body."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(
            host="voys.getklai.com",
            path="/api/me",
            session_org_id=42,
            accept="application/json",
        )
        call_next = AsyncMock()

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "getklai"})):
            response = await middleware.dispatch(request, call_next)

        call_next.assert_not_awaited()
        assert response.status_code == 409
        body = json.loads(response.body)
        assert body == {
            "detail": {
                "error_code": "tenant_host_mismatch",
                "redirect_to": "https://getklai.getklai.com/api/me",
            }
        }

    @pytest.mark.asyncio
    async def test_xhr_request_with_query_string(self) -> None:
        """409 JSON's redirect_to preserves the query string."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(
            host="voys.getklai.com",
            path="/api/me",
            query="x=1",
            session_org_id=42,
            accept="application/json",
        )
        call_next = AsyncMock()

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "getklai"})):
            response = await middleware.dispatch(request, call_next)

        body = json.loads(response.body)
        assert body["detail"]["redirect_to"] == "https://getklai.getklai.com/api/me?x=1"

    @pytest.mark.asyncio
    async def test_accept_with_both_html_and_json_returns_json(self) -> None:
        """Ambiguous Accept (e.g. */*) defaults to JSON 409 — safer for SPAs."""
        middleware = KlaiTenantHostMiddleware(app=MagicMock())
        request = _make_request(
            host="voys.getklai.com",
            path="/api/me",
            session_org_id=42,
            accept="text/html, application/json, */*",
        )
        call_next = AsyncMock()

        with patch.object(mw_module, "_resolve_org_slug", _slug_resolver({42: "getklai"})):
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Slug-cache behaviour
# ---------------------------------------------------------------------------


class TestSlugCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self) -> None:
        """Second request for the same org_id hits the in-memory cache."""
        # Pre-populate the cache so we know the lookup path is short-circuited.
        import time as time_mod

        mw_module._slug_cache[7] = ("acme", time_mod.monotonic() + 60)

        result = await mw_module._resolve_org_slug(7)
        assert result == "acme"
