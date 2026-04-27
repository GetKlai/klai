"""SPEC-SEC-005: Internal endpoint hardening tests.

Covers the three requirement groups:
- REQ-1: per-caller-IP rate limit (100 rpm, configurable, fail-open on Redis outage)
- REQ-2: fire-and-forget audit log write to portal_audit_log
- AC-5/AC-8: token check is the first gate; unauthenticated traffic does not consume
  rate-limit budget or produce audit rows.

Tests live alongside existing internal API tests and reuse the helpers fixture set.
Redis is mocked with an in-memory ZSET; AsyncSessionLocal is mocked to capture audit
params without requiring a live database connection.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@contextmanager
def _settings(**overrides):
    """Patch settings values for the duration of a test."""
    from app.api import internal as internal_mod

    originals = {}
    for key, value in overrides.items():
        originals[key] = getattr(internal_mod.settings, key)
        setattr(internal_mod.settings, key, value)
    try:
        yield
    finally:
        for key, value in originals.items():
            setattr(internal_mod.settings, key, value)


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------


def _make_redis_mock():
    """In-memory ZSET Redis mock compatible with app.services.partner_rate_limit."""
    store: dict[str, list[tuple[float, str]]] = {}

    async def zremrangebyscore(key, min_score, max_score):
        if key in store:
            store[key] = [(s, m) for s, m in store[key] if not (min_score <= s <= max_score)]
        return 0

    async def zcard(key):
        return len(store.get(key, []))

    async def zadd(key, mapping):
        if key not in store:
            store[key] = []
        for member, score in mapping.items():
            store[key].append((score, member))
        return 1

    async def expire(_key, _seconds):
        return True

    redis = AsyncMock()
    redis.zremrangebyscore = AsyncMock(side_effect=zremrangebyscore)
    redis.zcard = AsyncMock(side_effect=zcard)
    redis.zadd = AsyncMock(side_effect=zadd)
    redis.expire = AsyncMock(side_effect=expire)
    redis._store = store
    return redis


@pytest.fixture
def redis_pool():
    """Clean in-memory Redis mock per test."""
    return _make_redis_mock()


# ---------------------------------------------------------------------------
# Request mock
# ---------------------------------------------------------------------------


def _make_request(
    *,
    token: str | None = "secret-42",
    caller_ip: str | None = "172.18.0.5",
    xff: str | None = None,
    method: str = "GET",
    path: str = "/internal/user-language",
    route_path: str | None = "/internal/user-language",
) -> MagicMock:
    """Build a FastAPI Request mock compatible with _require_internal_token."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if xff is not None:
        headers["x-forwarded-for"] = xff
        headers["X-Forwarded-For"] = xff  # case-insensitive lookup in MagicMock

    request = MagicMock()
    # Make headers.get case-insensitive
    request.headers = MagicMock()
    request.headers.get = lambda key, default="": next(
        (v for k, v in headers.items() if k.lower() == key.lower()),
        default,
    )
    request.client = MagicMock()
    request.client.host = caller_ip
    request.method = method

    url = MagicMock()
    url.path = path
    request.url = url

    # request.scope.get("route") returns a route-like object with `.path`
    route = MagicMock()
    route.path = route_path
    request.scope = {"route": route} if route_path is not None else {}

    # request.state is a simple namespace that allows attribute assignment
    request.state = MagicMock()
    return request


# ---------------------------------------------------------------------------
# Audit session mock
# ---------------------------------------------------------------------------


def _make_audit_session(capture: list[dict], fail: bool = False) -> MagicMock:
    """AsyncSessionLocal() mock that records params passed to session.execute.

    When fail=True the execute() raises to simulate a DB outage — the audit
    helper must swallow and continue (REQ-2.4).
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def _execute(_sql, params):
        if fail:
            raise RuntimeError("DB down")
        capture.append(params)

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# _resolve_caller_ip
# ---------------------------------------------------------------------------


class TestResolveCallerIp:
    def test_uses_rightmost_xff_entry(self):
        from app.api.internal import _resolve_caller_ip

        request = _make_request(xff="1.1.1.1, 2.2.2.2, 172.18.0.5")
        assert _resolve_caller_ip(request) == "172.18.0.5"

    def test_falls_back_to_client_host(self):
        from app.api.internal import _resolve_caller_ip

        request = _make_request(caller_ip="10.0.0.1")
        assert _resolve_caller_ip(request) == "10.0.0.1"

    def test_returns_unknown_when_nothing_available(self):
        from app.api.internal import _resolve_caller_ip

        request = _make_request(caller_ip=None)
        request.client = None
        assert _resolve_caller_ip(request) == "unknown"


# ---------------------------------------------------------------------------
# Token check first gate (AC-5, AC-8)
# ---------------------------------------------------------------------------


class TestTokenFirstGate:
    """Token validation MUST precede rate-limit check and audit write."""

    @pytest.mark.asyncio
    async def test_wrong_token_does_not_consume_rate_limit(self, redis_pool):
        from app.api import internal as internal_mod

        request = _make_request(token="wrong", caller_ip="172.18.0.7")

        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            with pytest.raises(HTTPException) as exc:
                await internal_mod._require_internal_token(request)
            assert exc.value.status_code == 401

        # No rate-limit key added — unauthenticated traffic does not consume budget.
        # The internal limiter stores keys like `partner_rl:internal_rl:<ip>`.
        assert not any("internal_rl:" in key for key in redis_pool._store)

    @pytest.mark.asyncio
    async def test_wrong_token_does_not_write_audit_row(self):
        from app.api import internal as internal_mod

        captured: list[dict] = []
        audit_session = _make_audit_session(captured)

        request = _make_request(token="wrong")

        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.AsyncSessionLocal", return_value=audit_session),
        ):
            with pytest.raises(HTTPException):
                await internal_mod._require_internal_token(request)

        # No audit row scheduled — the helper returned before stashing context
        # and no handler ran _audit_internal_call.
        assert captured == []

    @pytest.mark.asyncio
    async def test_missing_secret_returns_503(self):
        from app.api import internal as internal_mod

        request = _make_request(token="whatever")
        with _settings(internal_secret=""):
            with pytest.raises(HTTPException) as exc:
                await internal_mod._require_internal_token(request)
            assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_correct_token_stashes_context(self, redis_pool):
        from app.api import internal as internal_mod

        request = _make_request(
            token="secret-42",
            caller_ip="172.18.0.9",
            path="/internal/v1/gap-events",
            route_path="/internal/v1/gap-events",
            method="POST",
        )

        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            await internal_mod._require_internal_token(request)

        assert request.state.internal_caller_ip == "172.18.0.9"
        assert request.state.internal_endpoint_path == "/internal/v1/gap-events"
        assert request.state.internal_method == "POST"


# ---------------------------------------------------------------------------
# Rate limit (REQ-1, AC-4, AC-6, AC-7)
# ---------------------------------------------------------------------------


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_under_limit_allowed(self, redis_pool):
        from app.api import internal as internal_mod

        request = _make_request(token="secret-42")
        with (
            _settings(internal_secret="secret-42", internal_rate_limit_rpm=100),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            # Should not raise for first request
            await internal_mod._require_internal_token(request)

    @pytest.mark.asyncio
    async def test_exceeding_limit_returns_429_with_retry_after(self, redis_pool):
        """When ceiling is 3 and 3 requests already made, the 4th returns 429."""
        from app.api import internal as internal_mod

        request = _make_request(token="secret-42", caller_ip="172.18.0.5")

        # Pre-fill the sliding window with 3 recent entries.
        # check_rate_limit prefixes its own key with `partner_rl:`, so the full key
        # for caller IP 172.18.0.5 is `partner_rl:internal_rl:172.18.0.5`.
        now = time.time()
        redis_pool._store["partner_rl:internal_rl:172.18.0.5"] = [(now - i * 0.1, f"req-{i}") for i in range(3)]

        with (
            _settings(internal_secret="secret-42", internal_rate_limit_rpm=3),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            with pytest.raises(HTTPException) as exc:
                await internal_mod._require_internal_token(request)

        assert exc.value.status_code == 429
        assert exc.value.detail == "Internal rate limit exceeded"
        retry_after = exc.value.headers["Retry-After"]
        assert int(retry_after) > 0

    @pytest.mark.asyncio
    async def test_distinct_caller_ips_isolated(self, redis_pool):
        """Rate limit is per-IP, not global — different IPs share no budget."""
        from app.api import internal as internal_mod

        with (
            _settings(internal_secret="secret-42", internal_rate_limit_rpm=2),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            # Fill IP A's budget
            for _ in range(2):
                await internal_mod._require_internal_token(_make_request(token="secret-42", caller_ip="172.18.0.10"))
            # IP B should still be permitted (different key namespace)
            await internal_mod._require_internal_token(_make_request(token="secret-42", caller_ip="172.18.0.11"))

        # check_rate_limit prefixes its own key with `partner_rl:`.
        assert "partner_rl:internal_rl:172.18.0.10" in redis_pool._store
        assert "partner_rl:internal_rl:172.18.0.11" in redis_pool._store

    @pytest.mark.asyncio
    async def test_redis_unavailable_fails_open(self):
        """REQ-1.3 / AC-6: no Redis pool → request is allowed, warning is logged."""
        from app.api import internal as internal_mod

        request = _make_request(token="secret-42")
        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.get_redis_pool", return_value=None),
        ):
            # Must NOT raise — fail-open path
            await internal_mod._require_internal_token(request)

    @pytest.mark.asyncio
    async def test_redis_raises_fails_open(self):
        """REQ-1.3 / AC-6: Redis call raising → request is allowed, warning is logged."""
        from app.api import internal as internal_mod

        broken_redis = AsyncMock()
        broken_redis.zremrangebyscore = AsyncMock(side_effect=RuntimeError("connection refused"))

        request = _make_request(token="secret-42")
        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.get_redis_pool", return_value=broken_redis),
        ):
            # Must NOT raise
            await internal_mod._require_internal_token(request)

    @pytest.mark.asyncio
    async def test_ceiling_is_configurable_via_settings(self, redis_pool):
        """REQ-1.7 / AC-7: lowering internal_rate_limit_rpm lowers the effective ceiling."""
        from app.api import internal as internal_mod

        request = _make_request(token="secret-42", caller_ip="172.18.0.77")
        now = time.time()
        # Pre-fill with exactly 5 entries (see partner_rl:* prefix note above).
        redis_pool._store["partner_rl:internal_rl:172.18.0.77"] = [(now - i * 0.1, f"req-{i}") for i in range(5)]

        # With rpm=5 the next request must be denied
        with (
            _settings(internal_secret="secret-42", internal_rate_limit_rpm=5),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            with pytest.raises(HTTPException) as exc:
                await internal_mod._require_internal_token(request)
            assert exc.value.status_code == 429


# ---------------------------------------------------------------------------
# Audit log (REQ-2, AC-1, AC-2, AC-12)
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_log_internal_call_inserts_correct_params(self):
        """REQ-2.1: audit row contains org_id, actor, action, resource_type, resource_id, details."""
        from app.api import internal as internal_mod

        captured: list[dict] = []
        session = _make_audit_session(captured)

        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            await internal_mod._log_internal_call(
                org_id=42,
                caller_ip="172.18.0.5",
                endpoint_path="/internal/v1/gap-events",
                method="POST",
            )

        assert len(captured) == 1
        params = captured[0]
        assert params["org_id"] == 42
        assert params["actor_user_id"] == "internal:172.18.0.5"
        assert params["action"] == "internal_call"
        assert params["resource_type"] == "internal_endpoint"
        assert params["resource_id"] == "/internal/v1/gap-events"
        assert session.commit.await_count == 1

    @pytest.mark.asyncio
    async def test_log_internal_call_uses_zero_for_unresolved_org(self):
        """REQ-2.1 / AC-3: unresolved org_id → stored as 0."""
        from app.api import internal as internal_mod

        captured: list[dict] = []
        session = _make_audit_session(captured)

        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            await internal_mod._log_internal_call(
                org_id=None,
                caller_ip="172.18.0.5",
                endpoint_path="/internal/user-language",
                method="GET",
            )

        assert captured[0]["org_id"] == 0

    @pytest.mark.asyncio
    async def test_details_contains_only_caller_ip_and_method(self):
        """AC-12: details JSONB must contain only caller_ip and method — no PII."""
        from app.api import internal as internal_mod

        captured: list[dict] = []
        session = _make_audit_session(captured)

        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            await internal_mod._log_internal_call(
                org_id=7,
                caller_ip="172.18.0.5",
                endpoint_path="/internal/user-language",
                method="GET",
            )

        details = json.loads(captured[0]["details"])
        assert set(details.keys()) == {"caller_ip", "method"}
        assert details == {"caller_ip": "172.18.0.5", "method": "GET"}

    @pytest.mark.asyncio
    async def test_audit_failure_is_non_fatal(self):
        """REQ-2.4 / AC-1: audit insert failure MUST NOT raise."""
        from app.api import internal as internal_mod

        session = _make_audit_session(capture=[], fail=True)

        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            # Must not raise
            await internal_mod._log_internal_call(
                org_id=1,
                caller_ip="172.18.0.5",
                endpoint_path="/internal/user-language",
                method="GET",
            )

    @pytest.mark.asyncio
    async def test_resource_id_is_route_template_not_raw_url(self):
        """REQ-2.5: resource_id must be the matched route template (no query string)."""
        from app.api import internal as internal_mod

        captured: list[dict] = []
        session = _make_audit_session(captured)

        # Endpoint path should never include a query string
        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            await internal_mod._log_internal_call(
                org_id=1,
                caller_ip="172.18.0.5",
                endpoint_path="/internal/v1/orgs/{org_id}/page-saved",
                method="POST",
            )

        assert "?" not in captured[0]["resource_id"]
        assert "{org_id}" in captured[0]["resource_id"]


# ---------------------------------------------------------------------------
# End-to-end fire-and-forget behaviour (AC-2)
# ---------------------------------------------------------------------------


class TestFireAndForget:
    @pytest.mark.asyncio
    async def test_audit_call_is_scheduled_as_background_task(self, redis_pool):
        """REQ-2.3 / AC-2: _audit_internal_call schedules work on a background task,
        using an independent session so an exception in the primary handler does not
        roll back the audit.
        """
        from app.api import internal as internal_mod

        request = _make_request(token="secret-42", caller_ip="172.18.0.5")

        # First: token check passes, context stashed
        with (
            _settings(internal_secret="secret-42"),
            patch("app.api.internal.get_redis_pool", return_value=redis_pool),
        ):
            await internal_mod._require_internal_token(request)

        # Simulate the handler scheduling the audit, then raising.
        captured: list[dict] = []
        session = _make_audit_session(captured)

        with patch("app.api.internal.AsyncSessionLocal", return_value=session):
            await internal_mod._audit_internal_call(request, org_id=42)

            # Wait briefly for the background task to execute.
            # Give up to 2s so this passes on slow CI workers.
            for _ in range(200):
                if captured:
                    break
                await asyncio.sleep(0.01)

        assert len(captured) == 1
        assert captured[0]["org_id"] == 42
        assert captured[0]["actor_user_id"] == "internal:172.18.0.5"
