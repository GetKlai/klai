"""SPEC-SEC-HYGIENE-001 REQ-39 / AC-39: /health event-loop + topology safety.

Verifies that the retrieval-api /health endpoint:

* REQ-39.1 — probes FalkorDB with an async PING so it does not block the
  event loop; ``test_graph_resilience.py`` proves that against a socket
  server that answers late.
* REQ-39.2 — returns the literal string ``"error"`` for failing dependency
  fields in the JSON body, never echoing the underlying exception
  message (which can include internal hostnames or IP addresses).
* REQ-39.3 — logs the full exception (with traceback) via ``exc_info``
  when a dependency check fails.
* REQ-39.4 — preserves the existing 503 status code when at least one
  dependency is unhealthy and 200 when all are healthy.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

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


class _FakeRedisOK:
    def __init__(self, *_a, **_kw): ...

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None: ...


class _FailRedis(_FakeRedisOK):
    async def ping(self) -> bool:
        raise ConnectionError("falkordb: no route to host 172.18.0.5:6379")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def all_deps_ok(monkeypatch):
    """Patch every dependency probed by /health to return success quickly."""
    import qdrant_client
    import redis.asyncio

    monkeypatch.setattr(httpx.AsyncClient, "get", _httpx_get_ok, raising=True)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _FakeQdrantOK)
    monkeypatch.setattr(redis.asyncio, "Redis", _FakeRedisOK)


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
    import qdrant_client
    import redis.asyncio
    from fastapi.testclient import TestClient

    from retrieval_api.config import settings
    from retrieval_api.main import app

    monkeypatch.setattr(settings, "graphiti_enabled", True, raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "get", _httpx_get_connect_error, raising=True)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _FakeQdrantFail)
    monkeypatch.setattr(redis.asyncio, "Redis", _FailRedis)

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
        assert needle not in body_str, f"/health response leaks topology via {needle!r}: {body_str}"

    # AC-39 step 7: structlog captures the full exception via exc_info
    exc_records = [rec for rec in log_capture if rec.exc_info is not None]
    assert exc_records, (
        "Expected at least one log record with exc_info attached for the "
        "/health dependency failures, found none. Without exc_info the "
        "exception detail is lost (REQ-39.3)."
    )
