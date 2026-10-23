"""Retrieval never waits on a sick graph (incident 2026-10-23).

FalkorDB segfaulted and reloaded its dump about every two minutes for seven
hours. Each reload took 99-105 s, and during it FalkorDB accepted connections
but answered nothing. retrieval-api requests then sat unanswered until the
reload ended (request 0060fd6f: sent 10:14:47, ``auth_accepted`` 10:15:24, the
same second FalkorDB logged "Ready to accept connections"), because
constructing a falkordb client sends a synchronous ``INFO`` from the event
loop. These tests run the real redis/falkordb clients against a socket server
that answers late, so a blocking call shows up as a stalled event loop.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from retrieval_api.services import graph_search

_REPLY_DELAY_S = 0.5
# Ticks every 10 ms; a gap this long means something held the event loop.
_MAX_LOOP_GAP_S = 0.2


class _SlowRedis:
    """A RESP server that answers every command after ``delay`` seconds."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        with conn:
            while True:
                try:
                    data = conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while (command := _pop_command(buf)) is not None:
                    name, buf = command
                    time.sleep(self.delay)
                    try:
                        conn.sendall(_reply(name))
                    except OSError:
                        return

    def close(self) -> None:
        self._sock.close()


def _pop_command(buf: bytes) -> tuple[str, bytes] | None:
    """Parse one RESP array off ``buf``; None until it is complete."""
    lines = buf.split(b"\r\n")
    if len(lines) < 2 or not lines[0].startswith(b"*"):
        return None
    parts = int(lines[0][1:])
    needed = 1 + 2 * parts
    if len(lines) <= needed:
        return None
    name = lines[2].decode().upper()
    rest = b"\r\n".join(lines[needed:])
    return name, rest


def _reply(name: str) -> bytes:
    if name == "PING":
        return b"+PONG\r\n"
    if name == "INFO":
        body = b"redis_mode:standalone\r\nrun_id:abc\r\n"
        return b"$%d\r\n%s\r\n" % (len(body), body)
    return b"+OK\r\n"


@pytest.fixture
def slow_falkordb(monkeypatch) -> Iterator[_SlowRedis]:
    from retrieval_api.config import settings

    server = _SlowRedis(_REPLY_DELAY_S)
    monkeypatch.setattr(settings, "falkordb_host", "127.0.0.1")
    monkeypatch.setattr(settings, "falkordb_port", server.port)
    monkeypatch.setattr(settings, "graphiti_enabled", True)
    yield server
    server.close()


async def _max_loop_gap(work) -> float:
    """Run ``work`` while a 10 ms ticker measures the longest loop stall."""
    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # let the ticker take its first timestamp
    try:
        await work
    finally:
        done.set()
        await tick
    return max(gaps, default=0.0)


async def test_health_with_unresponsive_falkordb_does_not_stall_other_requests(
    slow_falkordb, monkeypatch
):
    import qdrant_client

    class _Ok:
        status_code = 200

    async def _get_ok(self, url, headers=None, **_kw):
        return _Ok()

    class _Qdrant:
        def __init__(self, *_a, **_kw): ...

        async def get_collections(self):
            return []

    monkeypatch.setattr(httpx.AsyncClient, "get", _get_ok)
    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _Qdrant)

    from retrieval_api.main import app

    responses: list[httpx.Response] = []

    async def _health() -> None:
        # ``request``, not ``get``: ``get`` is patched above for /health's own probes.
        responses.append(await ac.request("GET", "/health"))

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        gap = await _max_loop_gap(_health())

    assert gap < _MAX_LOOP_GAP_S, f"/health held the event loop for {gap:.2f}s"
    assert responses[0].json()["falkordb"] == "ok"


async def test_graph_client_construction_does_not_stall_other_requests(slow_falkordb, monkeypatch):
    monkeypatch.setattr(graph_search, "_graphiti_client", None)
    # Production sets OPENAI_API_KEY=dummy for Graphiti's default cross-encoder.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setattr(graph_search.settings, "graph_search_timeout", 2.0)
    monkeypatch.setattr(graph_search.settings, "tei_url", "http://127.0.0.1:9")

    gap = await _max_loop_gap(graph_search.search("refund policy", "org-1"))

    assert gap < _MAX_LOOP_GAP_S, f"graph search held the event loop for {gap:.2f}s"
    if graph_search._graphiti_client is not None:
        await graph_search.close()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(graph_search, "_circuit", graph_search._GraphCircuit(clock=fake))
    monkeypatch.setattr(graph_search.settings, "graphiti_enabled", True)
    monkeypatch.setattr(graph_search.settings, "graph_search_timeout", 0.05)
    return fake


def _hanging_graphiti() -> MagicMock:
    async def _hang(*_a, **_kw):
        await asyncio.sleep(10)

    graphiti = MagicMock()
    graphiti.search = AsyncMock(side_effect=_hang)
    return graphiti


def _events(caplog) -> list[str]:
    return [r.msg.get("event") for r in caplog.records if isinstance(r.msg, dict)]


async def _fail_until_open(graphiti: MagicMock) -> None:
    with patch.object(graph_search, "_get_graphiti", return_value=graphiti):
        for _ in range(graph_search._CIRCUIT_FAILURES):
            assert await graph_search.search("refund policy", "org-1") == []


async def test_open_circuit_answers_without_touching_falkordb(clock, caplog):
    graphiti = _hanging_graphiti()
    with caplog.at_level("WARNING"):
        await _fail_until_open(graphiti)
    assert graphiti.search.await_count == graph_search._CIRCUIT_FAILURES
    assert _events(caplog).count("graph_circuit_open") == 1

    get_graphiti = MagicMock(return_value=graphiti)
    with patch.object(graph_search, "_get_graphiti", get_graphiti):
        started = time.monotonic()
        assert await graph_search.search("refund policy", "org-1") == []
        elapsed = time.monotonic() - started

    assert elapsed < 0.01
    get_graphiti.assert_not_called()
    assert graphiti.search.await_count == graph_search._CIRCUIT_FAILURES


async def test_after_cooldown_one_probe_runs_and_its_success_closes_the_circuit(clock, caplog):
    await _fail_until_open(_hanging_graphiti())
    clock.now += graph_search._CIRCUIT_COOLDOWN_S

    release = asyncio.Event()

    async def _answer(*_a, **_kw):
        await release.wait()
        return []

    healthy = MagicMock()
    healthy.search = AsyncMock(side_effect=_answer)
    with patch.object(graph_search, "_get_graphiti", return_value=healthy), caplog.at_level("INFO"):
        probe = asyncio.create_task(graph_search.search("refund policy", "org-1"))
        await asyncio.sleep(0.01)
        # While the probe is out, everyone else still skips the graph.
        assert await graph_search.search("refund policy", "org-1") == []
        assert healthy.search.await_count == 1
        release.set()
        assert await probe == []
        assert await graph_search.search("refund policy", "org-1") == []

    assert healthy.search.await_count == 2
    assert _events(caplog).count("graph_circuit_closed") == 1


def test_open_circuit_retrieve_serves_qdrant_results_without_graph_calls(
    client, sample_retrieve_request, clock
):
    graphiti = _hanging_graphiti()
    asyncio.run(_fail_until_open(graphiti))
    get_graphiti = MagicMock(return_value=graphiti)
    qdrant_chunk = {
        "chunk_id": "qdrant-1",
        "text": "Qdrant result",
        "score": 0.9,
        "artifact_id": "a1",
        "content_type": "policy",
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
        "ingested_at": None,
        "assertion_mode": None,
    }
    with (
        patch.object(graph_search, "_get_graphiti", get_graphiti),
        patch(
            "retrieval_api.api.retrieve.coreference.resolve",
            new_callable=AsyncMock,
            return_value="resolved query",
        ),
        patch(
            "retrieval_api.api.retrieve.embed_single",
            new_callable=AsyncMock,
            return_value=[0.1, 0.2],
        ),
        patch(
            "retrieval_api.api.retrieve.embed_sparse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "retrieval_api.api.retrieve.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=[qdrant_chunk],
        ),
        patch("retrieval_api.api.retrieve.settings") as mock_settings,
    ):
        mock_settings.ranking_contract_mode = "active"
        mock_settings.retrieval_candidates = 60
        mock_settings.reranker_enabled = False
        mock_settings.graphiti_enabled = True
        mock_settings.link_expand_enabled = False
        mock_settings.link_expand_score_boost = 1.0
        mock_settings.source_quota_enabled = False
        mock_settings.router_enabled = False
        mock_settings.retrieval_quality_floor = 0.05
        mock_settings.confidence_band_high_threshold = 0.60
        mock_settings.confidence_band_low_threshold = 0.30
        started = time.monotonic()
        response = client.post("/retrieve", json=sample_retrieve_request)
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert [chunk["chunk_id"] for chunk in response.json()["chunks"]] == ["qdrant-1"]
    assert elapsed < 1.0
    get_graphiti.assert_not_called()
