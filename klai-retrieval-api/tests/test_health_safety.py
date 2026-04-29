"""SPEC-SEC-HYGIENE-001 REQ-39 / AC-39: /health event-loop + topology safety.

Verifies that the retrieval-api /health endpoint:

* REQ-39.1 — wraps the synchronous FalkorDB ping in ``asyncio.to_thread``
  so it does not block the event loop. Concurrent /health calls run in
  parallel via the default thread pool.
* REQ-39.2 — returns the literal string ``"error"`` for failing dependency
  fields in the JSON body, never echoing the underlying exception
  message (which can include internal hostnames or IP addresses).
* REQ-39.3 — logs the full exception (with traceback) via ``exc_info``
  when a dependency check fails.
* REQ-39.4 — preserves the existing 503 status code when at least one
  dependency is unhealthy and 200 when all are healthy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

# --------------------------------------------------------------------------- #
# Fakes for the four dependencies probed by /health
# --------------------------------------------------------------------------- #


class _OK:
    status_code = 200


async def _httpx_get_ok(self, url: str, headers: dict[str, Any] | None = None, **_kw):
    return _OK()


async def _httpx_get_connect_error(self, url: str, headers: dict[str, Any] | None = None, **_kw):
    raise httpx.ConnectError(f"{url}: connection refused")


class _FakeQdrantOK:
    def __init__(self, *_a, **_kw): ...

    async def get_collections(self):
        return []


class _FakeQdrantFail:
    def __init__(self, *_a, **_kw): ...

    async def get_collections(self):
        raise ConnectionError("qdrant: no route to host 172.18.0.4:6333")


class _FakeFalkorOK:
    class _Conn:
        def ping(self) -> None: ...

    def __init__(self, *_a, **_kw):
        self.connection = self._Conn()


def _slow_falkor(sleep_for: float):
    class _Conn:
        def ping(self) -> None:
            time.sleep(sleep_for)

    class _Falkor:
        def __init__(self, *_a, **_kw):
            self.connection = _Conn()

    return _Falkor


class _FailFalkor:
    class _Conn:
        def ping(self) -> None:
            raise ConnectionError("falkordb: no route to host 172.18.0.5:6379")

    def __init__(self, *_a, **_kw):
        self.connection = self._Conn()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def all_deps_ok(monkeypatch):
    """Patch every dependency probed by /health to return success quickly."""
    import falkordb
    import qdrant_client

    monkeypatch.setattr(httpx.AsyncClient, "get", _httpx_get_ok, raising=True)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _FakeQdrantOK)
    monkeypatch.setattr(falkordb, "FalkorDB", _FakeFalkorOK)


@pytest.fixture
def log_capture():
    """Capture stdlib log records so we can inspect ``exc_info``.

    structlog routes through the stdlib logger at the root level
    (see ``logging_setup.setup_logging``); attaching a plain
    ``logging.Handler`` is the most reliable way to verify ``exc_info``
    propagation across the integration.
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


# --------------------------------------------------------------------------- #
# REQ-39.4 — status code preservation
# --------------------------------------------------------------------------- #


def test_health_returns_200_when_all_deps_ok(all_deps_ok, monkeypatch):
    """REQ-39.4: with every dep healthy, /health is 200."""
    from fastapi.testclient import TestClient

    from retrieval_api.config import settings
    from retrieval_api.main import app

    monkeypatch.setattr(settings, "graphiti_enabled", True, raising=False)

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tei"] == "ok"
    assert body["qdrant"] == "ok"
    assert body["litellm"] == "ok"
    assert body["falkordb"] == "ok"


def test_health_returns_503_when_one_dep_down(monkeypatch):
    """REQ-39.4: with at least one dep unhealthy, /health stays 503."""
    import qdrant_client
    from fastapi.testclient import TestClient

    from retrieval_api.config import settings
    from retrieval_api.main import app

    monkeypatch.setattr(settings, "graphiti_enabled", False, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "get", _httpx_get_connect_error, raising=True)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _FakeQdrantOK)

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 503


# --------------------------------------------------------------------------- #
# REQ-39.2 / REQ-39.3 — topology + exc_info
# --------------------------------------------------------------------------- #


def test_health_response_body_does_not_leak_internal_topology(monkeypatch, log_capture):
    """REQ-39.2: failing dependency fields say 'error' (literal), not the URL.

    REQ-39.3: the underlying exception MUST be captured via ``exc_info``
    on the structlog/stdlib log record, so operators can still debug.
    """
    import falkordb
    import qdrant_client
    from fastapi.testclient import TestClient

    from retrieval_api.config import settings
    from retrieval_api.main import app

    monkeypatch.setattr(settings, "graphiti_enabled", True, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "get", _httpx_get_connect_error, raising=True)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _FakeQdrantFail)
    monkeypatch.setattr(falkordb, "FalkorDB", _FailFalkor)

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 503

    body = r.json()
    # AC-39 step 6: every failing dep field is the literal "error", not "error: <url>"
    for dep in ("tei", "qdrant", "litellm", "falkordb"):
        assert body.get(dep) == "error", (
            f"{dep!r} response field leaks topology — expected 'error', got {body.get(dep)!r}"
        )

    # AC-39 step 6: no internal IPs / hostnames / verbose error strings anywhere in the body
    body_str = str(body)
    for needle in ("172.18.", "connection refused", "no route to host"):
        assert needle not in body_str, (
            f"/health response leaks topology via {needle!r}: {body_str}"
        )

    # AC-39 step 7: structlog captures the full exception via exc_info
    exc_records = [rec for rec in log_capture if rec.exc_info is not None]
    assert exc_records, (
        "Expected at least one log record with exc_info attached for the "
        "/health dependency failures, found none. Without exc_info the "
        "exception detail is lost (REQ-39.3)."
    )


# --------------------------------------------------------------------------- #
# REQ-39.1 — sync FalkorDB ping does not block the event loop
# --------------------------------------------------------------------------- #


async def test_health_falkordb_ping_does_not_block_event_loop(monkeypatch, all_deps_ok):
    """REQ-39.1: sync ``db.connection.ping()`` MUST run in a thread pool.

    The strategy: replace FalkorDB's ping with a 400 ms sleep and fire four
    concurrent /health requests on the same event loop via ``asyncio.gather``
    against an in-process ASGI transport. If the ping blocks the loop, total
    wall time approaches ``n * sleep = 1.6 s``. With ``asyncio.to_thread``
    the pings run concurrently in the default thread-pool executor, so total
    wall time stays close to a single ping (~0.4 s + scheduler overhead).
    """
    import falkordb

    from retrieval_api.config import settings
    from retrieval_api.main import app

    monkeypatch.setattr(settings, "graphiti_enabled", True, raising=False)

    sleep_secs = 0.4
    monkeypatch.setattr(falkordb, "FalkorDB", _slow_falkor(sleep_secs))

    transport = ASGITransport(app=app)
    n_concurrent = 4
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        start = time.monotonic()
        results = await asyncio.gather(*(ac.get("/health") for _ in range(n_concurrent)))
        elapsed = time.monotonic() - start

    assert all(r.status_code == 200 for r in results), [r.status_code for r in results]

    # Sequential (event loop blocked): n * sleep = 1.6 s.
    # Concurrent (asyncio.to_thread + thread pool >= n): max(sleep) + overhead ≈ 0.5 s.
    # Threshold = 60% of the sequential bound, generous to avoid CI flakes.
    upper_bound = (n_concurrent * sleep_secs) * 0.6
    assert elapsed < upper_bound, (
        f"Event loop appears blocked by sync FalkorDB ping: "
        f"{n_concurrent} concurrent /health calls took {elapsed:.2f}s "
        f"(expected < {upper_bound:.2f}s with asyncio.to_thread)."
    )
