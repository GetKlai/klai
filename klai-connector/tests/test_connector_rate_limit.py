"""SPEC-SEC-HYGIENE-001 HY-32 — per-org Redis sliding-window rate limit.

Pre-fix state: ``app/middleware/auth.py`` wraps every ``/api/v1/connectors*``
route in a Zitadel JWT check. Once a user is authenticated, they can
POST new connectors, fuzz UUIDs via GET/PUT/DELETE, and do so at
unbounded rate. Combined with HY-30's UUID-existence oracle (now fixed
in commit 10715d18), an authenticated attacker could enumerate per-tenant
connector UUIDs without any throttle, plus push unbounded rows into the
``connector.connectors`` table.

Post-fix state: a per-org Redis sorted-set sliding window bounds GET/LIST
(read) and POST/PUT/DELETE (write) traffic per org_id.

Defaults chosen via /run research (see PR description for sources):
    - read:  120/min/org  (≈ Auth0 free tier; > Heroku 75/min)
    - write:  30/min/org  (3× the SPEC literal; still 1800/hour ceiling)
The acceptance test below sets the limits to the SPEC's literal values
(60 read / 10 write) so it exercises the same boundary the SPEC text
describes — production tunes via env, no test rewrite needed.

Coverage matrix (AC-32):
    1. Write limit:  10 POSTs ok → 11th 429
    2. Reset:        clock +61s → 12th POST ok
    3. Read limit:   60 GETs ok → 61st 429
    4. Read reset:   clock +61s → 62nd GET ok
    5. Fail-open:    Redis raises → request allowed +
                     ``connector_rate_limit_redis_unavailable`` event emitted
    6. Cross-tenant: org_1 hits limit → org_2 unaffected
    7. Portal bypass: ``from_portal=True`` skips the check (control-plane
                      traffic uses portal_caller_secret bypass — not
                      user-quota traffic).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_session
from app.routes.connectors import router as connectors_router
from app.routes.deps import get_redis_client, get_settings

_DEFAULT_READ_LIMIT = 60
_DEFAULT_WRITE_LIMIT = 10


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory ZSET subset — only what check_rate_limit calls."""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self._fail_with: Exception | None = None

    def fail_with(self, exc: Exception | None) -> None:
        self._fail_with = exc

    def _maybe_fail(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    async def zremrangebyscore(
        self, key: str, min_score: float, max_score: float
    ) -> int:
        self._maybe_fail()
        zset = self._zsets.setdefault(key, {})
        to_remove = [m for m, s in zset.items() if min_score <= s <= max_score]
        for m in to_remove:
            del zset[m]
        return len(to_remove)

    async def zcard(self, key: str) -> int:
        self._maybe_fail()
        return len(self._zsets.get(key, {}))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._maybe_fail()
        zset = self._zsets.setdefault(key, {})
        added = sum(1 for k in mapping if k not in zset)
        zset.update(mapping)
        return added

    async def expire(self, key: str, seconds: int) -> bool:
        self._maybe_fail()
        return True


class _FakeSession:
    """Minimal AsyncSession stub — accepts the create/list/get/put/delete
    paths without touching a database. The tests never exercise the
    persistence semantics; they only care about HTTP status codes.
    """

    async def get(self, _model: Any, key: uuid.UUID) -> Any:
        return None  # all GETs/PUTs/DELETEs of arbitrary UUIDs miss

    async def execute(self, _stmt: Any) -> Any:
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))

    def add(self, obj: Any) -> None:
        # The route reads obj.id back via response_model — give it a UUID.
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        obj.created_at = now
        obj.updated_at = now
        obj.last_sync_at = None
        obj.last_sync_status = None
        obj.is_enabled = True

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None

    async def delete(self, _obj: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Test app builder
# ---------------------------------------------------------------------------


def _build_client(
    *,
    redis: _FakeRedis,
    org_id: str = "org-1",
    from_portal: bool = False,
    read_limit: int = _DEFAULT_READ_LIMIT,
    write_limit: int = _DEFAULT_WRITE_LIMIT,
) -> tuple[TestClient, dict[str, Any]]:
    """Build a FastAPI client wired with overrides + a state-mutating
    middleware. Returns (client, state) where ``state`` lets the test
    flip ``org_id`` / ``from_portal`` mid-test without rebuilding the app.
    """
    app = FastAPI()
    app.include_router(connectors_router, prefix="/api/v1")

    state = {"org_id": org_id, "from_portal": from_portal}

    @app.middleware("http")
    async def _set_request_state(request: Any, call_next: Any) -> Any:
        request.state.org_id = state["org_id"]
        request.state.from_portal = state["from_portal"]
        return await call_next(request)

    settings_stub = SimpleNamespace(
        connector_rl_read_per_min=read_limit,
        connector_rl_write_per_min=write_limit,
        redis_url="redis://test",  # non-empty so the dep returns the fake
    )

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: settings_stub
    app.dependency_overrides[get_redis_client] = lambda: redis

    return TestClient(app, raise_server_exceptions=False), state


# ---------------------------------------------------------------------------
# Clock control
# ---------------------------------------------------------------------------


@contextmanager
def _frozen_clock(monkeypatch: pytest.MonkeyPatch, t: list[float]):
    """Patch app.services.rate_limit._now with a list-backed clock.

    Tests advance the clock by mutating ``t[0]``.
    """
    from app.services import rate_limit as rl

    monkeypatch.setattr(rl, "_now", lambda: t[0])
    yield


def _post_connector(client: TestClient, name: str = "x") -> int:
    return client.post(
        "/api/v1/connectors",
        json={"name": name, "connector_type": "github", "config": {}},
    ).status_code


def _list_connectors(client: TestClient) -> int:
    return client.get("/api/v1/connectors").status_code


# ---------------------------------------------------------------------------
# AC-32 matrix
# ---------------------------------------------------------------------------


def test_write_limit_blocks_after_10_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-32.2 / REQ-32.4: 10 POSTs in a window → 11th returns 429."""
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis)
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for i in range(_DEFAULT_WRITE_LIMIT):
            assert _post_connector(client, f"c{i}") == 201, (
                f"POST #{i + 1} unexpectedly failed within the limit"
            )
        # The 11th call inside the same window must be rejected.
        assert _post_connector(client, "over") == 429
        body = client.post(
            "/api/v1/connectors",
            json={"name": "over2", "connector_type": "github", "config": {}},
        ).json()
        assert body == {"detail": "rate limit exceeded"}


def test_write_limit_resets_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """The window slides — at +61s the next POST is allowed."""
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis)
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for i in range(_DEFAULT_WRITE_LIMIT):
            _post_connector(client, f"c{i}")
        assert _post_connector(client, "over") == 429

        # Advance past the 60-second window — the oldest entries fall off.
        t[0] += 61.0
        assert _post_connector(client, "after-reset") == 201


def test_read_limit_blocks_after_60_gets(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-32.2: 60 GETs ok → 61st returns 429."""
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis)
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for i in range(_DEFAULT_READ_LIMIT):
            assert _list_connectors(client) == 200, f"GET #{i + 1} blocked unexpectedly"
        assert _list_connectors(client) == 429


def test_read_limit_resets_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis)
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for _ in range(_DEFAULT_READ_LIMIT):
            _list_connectors(client)
        assert _list_connectors(client) == 429
        t[0] += 61.0
        assert _list_connectors(client) == 200


# ---------------------------------------------------------------------------
# AC-32 cross-tenant isolation
# ---------------------------------------------------------------------------


def test_cross_tenant_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-32.2: an org-1 burst does not affect org-2."""
    redis = _FakeRedis()
    client, state = _build_client(redis=redis, org_id="org-1")
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for _ in range(_DEFAULT_WRITE_LIMIT):
            _post_connector(client)
        assert _post_connector(client) == 429  # org-1 is over

        state["org_id"] = "org-2"
        assert _post_connector(client) == 201  # org-2 has its own bucket


# ---------------------------------------------------------------------------
# AC-32 fail-open + structlog event
# ---------------------------------------------------------------------------


def test_redis_unavailable_fails_open_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-32.3: Redis raise → request allowed + structlog warning."""
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis)
    redis.fail_with(ConnectionError("redis://test: connection refused"))

    with structlog.testing.capture_logs() as captured:
        status = _post_connector(client)

    assert status == 201, f"expected fail-open 201, got {status}"
    events = [e["event"] for e in captured]
    assert "connector_rate_limit_redis_unavailable" in events, (
        f"expected fail-open event in structlog output, got: {events!r}"
    )


# ---------------------------------------------------------------------------
# Portal-secret bypass — control plane is not user traffic
# ---------------------------------------------------------------------------


def test_portal_secret_bypasses_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """When request.state.from_portal is True (portal_caller_secret bypass
    in auth middleware), the rate limit is skipped — portal control plane
    is not user traffic and is already authenticated by shared secret.
    """
    redis = _FakeRedis()
    client, _ = _build_client(redis=redis, from_portal=True, org_id="")
    t = [1000.0]

    with _frozen_clock(monkeypatch, t):
        for _ in range(_DEFAULT_WRITE_LIMIT * 3):  # well past the limit
            assert _post_connector(client) == 201
