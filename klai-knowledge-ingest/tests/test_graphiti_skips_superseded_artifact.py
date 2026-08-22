"""A graphiti job for a superseded artifact must not write its frozen text.

Contract: the graph may only hold episodes for the version of a document that
is currently active. A queued ``ingest_graphiti_episode`` carries the document
text as a task argument, frozen at enqueue time, so a job that is dequeued
after a newer ingest superseded its artifact would store stale content.

Why this became user-visible in #1152: episodes used to be named after the
artifact_id, so a stale episode was simply unresolvable -- a dead end. Since
episodes are named ``doc:<kb_slug>:<path>``, the stale episode shares its name
with the ACTIVE version, so retrieval resolves it and cites the live page URL
for content that only ever existed in the superseded version. Wrong attribution
is worse than no attribution.

The enrichment task already guards this exact window
(``enrichment_aborted_artifact_superseded``, enrichment_tasks.py). The graphiti
task guarded only on ``artifact_exists``, which stays true for superseded rows
because they are retained, so the guard never fired.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import knowledge_ingest
from knowledge_ingest import enrichment_tasks, queues

_ARTIFACT_ID = "0f9c1a2b-3d4e-4f50-9a61-72b83c94d5e6"
_ORG_ID = "368884765035593759"


class _CapturingApp:
    """Registers tasks without running them; keeps the decorated functions."""

    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self._queue_by_name: dict[str, str] = {}

    def task(self, **kwargs):
        queue = str(kwargs["queue"])

        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            self._queue_by_name[fn.__name__] = queue
            return fn

        return _decorator

    def queue_of(self, name: str) -> str:
        return self._queue_by_name[name]


@pytest.fixture
def graphiti_task() -> object:
    app = _CapturingApp()
    enrichment_tasks._register_tasks(app)
    assert app.queue_of("ingest_graphiti_episode") == queues.GRAPHITI_BULK
    return app.tasks["ingest_graphiti_episode"]


@asynccontextmanager
async def _fake_conn(org_id):
    assert org_id == _ORG_ID
    yield SimpleNamespace()


async def _run(graphiti_task, *, exists: bool, active: bool):
    """Run the task against a stubbed pg_store; return the ingest_episode mock."""
    ingest_episode = AsyncMock(return_value="episode-uuid")
    graph_module = MagicMock()
    graph_module.ingest_episode = ingest_episode

    pg_store = MagicMock()
    pg_store.artifact_exists = AsyncMock(return_value=exists)
    pg_store.artifact_is_active = AsyncMock(return_value=active)
    pg_store.update_artifact_extra = AsyncMock(return_value=None)

    # The task body does `from knowledge_ingest import pg_store` / `import graph
    # as graph_module`, which reads the PACKAGE attribute -- patching
    # sys.modules leaves the already-bound real submodule in place.
    with (
        patch.object(knowledge_ingest, "pg_store", pg_store),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _fake_conn),
    ):
        await graphiti_task(
            artifact_id=_ARTIFACT_ID,
            document_text="content that only existed in the superseded version",
            org_id=_ORG_ID,
            content_type="text/markdown",
            belief_time_start=0,
        )
    return ingest_episode


@pytest.mark.asyncio
async def test_superseded_artifact_does_not_reach_the_graph(graphiti_task):
    """The row still exists, but a newer ingest replaced it -- abort."""
    ingest_episode = await _run(graphiti_task, exists=True, active=False)
    ingest_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_artifact_still_reaches_the_graph(graphiti_task):
    """The guard must not break the normal path it is wrapped around."""
    ingest_episode = await _run(graphiti_task, exists=True, active=True)
    ingest_episode.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_artifact_still_aborts(graphiti_task):
    """SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07 must keep holding."""
    ingest_episode = await _run(graphiti_task, exists=False, active=False)
    ingest_episode.assert_not_awaited()
