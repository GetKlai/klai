"""A graphiti job that kills FalkorDB is quarantined, not retried forever.

On 23 October 2026 one document's extraction segfaulted FalkorDB; every crash
cost a 99-105 s reload, and the job's retries (three inside ingest_episode,
eight more from procrastinate, then graph_refresh) kept crashing it for seven
hours. A job counts as a suspect only when FalkorDB was up when its own
add_episode started and had restarted (new run_id, or no answer) after it
failed. A FalkorDB that was already down at the start says nothing about the
document, so those jobs keep the normal retries.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

import knowledge_ingest
from knowledge_ingest import enrichment_tasks
from knowledge_ingest import graph as graph_module
from knowledge_ingest.falkordb_probe import FalkorDBDiedDuringEpisode

_ARTIFACT_ID = "5b0d7c1e-2f3a-4b5c-8d6e-7f8091a2b3c4"
_ORG_ID = "380000000000000001"
_KB_SLUG = "handbook"


async def test_run_id_comes_from_a_live_server_and_none_from_a_dead_one(monkeypatch):
    """The real redis client against a socket: INFO's run_id, or None."""
    from knowledge_ingest import falkordb_probe

    async def _answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(65536):
            for command in data.split(b"*")[1:]:
                if b"INFO" in command.upper():
                    body = b"# Server\r\nrun_id:5f3c9e\r\n"
                    writer.write(b"$%d\r\n%s\r\n" % (len(body), body))
                else:
                    writer.write(b"+OK\r\n")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(_answer, "127.0.0.1", 0)
    monkeypatch.setattr(falkordb_probe.settings, "falkordb_host", "127.0.0.1")
    monkeypatch.setattr(
        falkordb_probe.settings, "falkordb_port", server.sockets[0].getsockname()[1]
    )
    async with server:
        assert await falkordb_probe.falkordb_run_id() == "5f3c9e"

    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        monkeypatch.setattr(falkordb_probe.settings, "falkordb_port", closed.getsockname()[1])
    assert await falkordb_probe.falkordb_run_id() is None


async def _ingest(graphiti: AsyncMock) -> str | None:
    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=graphiti),
        patch("knowledge_ingest.graph.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
        return await graph_module.ingest_episode(
            artifact_id=_ARTIFACT_ID,
            document_text="Hello",
            org_id=_ORG_ID,
            content_type="text",
            belief_time_start=1700000000,
        )


async def test_falkordb_restart_during_add_episode_stops_the_inner_retries():
    graphiti = AsyncMock()
    graphiti.add_episode = AsyncMock(
        side_effect=ConnectionError("Error while reading from falkordb:6379 : (104, reset)")
    )
    run_ids = AsyncMock(side_effect=["run-before", "run-after-restart"])

    with (
        patch("knowledge_ingest.graph.falkordb_run_id", run_ids),
        pytest.raises(FalkorDBDiedDuringEpisode),
    ):
        await _ingest(graphiti)

    assert graphiti.add_episode.await_count == 1


async def test_falkordb_down_at_start_keeps_the_normal_retries():
    graphiti = AsyncMock()
    graphiti.add_episode = AsyncMock(
        side_effect=ConnectionError("Error 111 connecting to falkordb:6379. Connection refused.")
    )

    with patch("knowledge_ingest.graph.falkordb_run_id", AsyncMock(return_value=None)):
        assert await _ingest(graphiti) is None

    assert graphiti.add_episode.await_count == 3


class _CapturingApp:
    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {}
        self.retry: dict[str, Any] = {}

    def task(self, **kwargs):
        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            self.retry[fn.__name__] = kwargs.get("retry")
            return fn

        return _decorator


@asynccontextmanager
async def _fake_conn(org_id):
    yield SimpleNamespace()


class _Artifact:
    """One artifact row whose ``extra`` survives across job runs."""

    def __init__(self) -> None:
        self.extra: dict[str, Any] = {"document_text": "Some document text."}

    async def read(self, _conn, _artifact_id):
        return {"extra": dict(self.extra)}

    async def update_extra(self, _conn, _artifact_id, patch_: dict[str, Any]):
        self.extra.update(patch_)


async def _run_job(task, artifact: _Artifact, ingest_episode: AsyncMock, attempts: int) -> None:
    graph = MagicMock()
    graph.ingest_episode = ingest_episode
    graph.EntityGraphData.return_value = SimpleNamespace()
    graph.flush_entity_graph_data = AsyncMock(return_value=None)

    pg_store = MagicMock()
    pg_store.read_artifact_for_enrichment = AsyncMock(side_effect=artifact.read)
    pg_store.artifact_exists = AsyncMock(return_value=True)
    pg_store.artifact_is_active = AsyncMock(return_value=True)
    pg_store.update_artifact_extra = AsyncMock(side_effect=artifact.update_extra)
    pg_store.append_graphiti_episode_id = AsyncMock(return_value=None)

    with (
        patch.object(knowledge_ingest, "pg_store", pg_store),
        patch.object(knowledge_ingest, "graph", graph),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _fake_conn),
    ):
        await task(
            SimpleNamespace(job=SimpleNamespace(attempts=attempts)),
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
            content_type="text/markdown",
            belief_time_start=0,
            kb_slug=_KB_SLUG,
        )


@pytest.fixture
def app() -> _CapturingApp:
    captured = _CapturingApp()
    enrichment_tasks._register_tasks(captured)
    return captured


async def test_job_that_kills_falkordb_twice_is_quarantined_and_not_requeued(app):
    task = app.tasks["ingest_graphiti_episode"]
    retry = app.retry["ingest_graphiti_episode"]
    artifact = _Artifact()
    killer = AsyncMock(side_effect=FalkorDBDiedDuringEpisode("connection reset"))

    with pytest.raises(FalkorDBDiedDuringEpisode) as first:
        await _run_job(task, artifact, killer, attempts=0)
    assert retry.get_retry_decision(exception=first.value, job=SimpleNamespace(attempts=0))

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(enrichment_tasks.GraphitiEpisodeQuarantined) as second,
    ):
        await _run_job(task, artifact, killer, attempts=1)
    assert retry.get_retry_decision(exception=second.value, job=SimpleNamespace(attempts=1)) is None

    quarantined = [e for e in logs if e["event"] == "graphiti_episode_quarantined"]
    assert len(quarantined) == 1
    assert quarantined[0]["artifact_id"] == _ARTIFACT_ID
    assert quarantined[0]["org_id"] == _ORG_ID
    assert quarantined[0]["kb_slug"] == _KB_SLUG
    assert artifact.extra["graphiti_quarantined_at"]

    # graph_refresh or a re-sync enqueueing it again must not reach FalkorDB.
    killer.reset_mock()
    await _run_job(task, artifact, killer, attempts=0)
    killer.assert_not_awaited()


async def test_job_that_finds_falkordb_down_at_start_is_retried_as_before(app):
    task = app.tasks["ingest_graphiti_episode"]
    retry = app.retry["ingest_graphiti_episode"]
    artifact = _Artifact()
    down = AsyncMock(return_value=None)

    for attempt in range(3):
        with pytest.raises(RuntimeError) as failure:
            await _run_job(task, artifact, down, attempts=attempt)
        assert not isinstance(failure.value, FalkorDBDiedDuringEpisode)
        assert retry.get_retry_decision(
            exception=failure.value, job=SimpleNamespace(attempts=attempt)
        )

    assert "graphiti_quarantined_at" not in artifact.extra
