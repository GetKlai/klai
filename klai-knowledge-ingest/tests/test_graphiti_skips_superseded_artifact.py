"""A graphiti job for a superseded artifact must not write stale text.

Contract: the graph may only hold episodes for the version of a document that
is currently active. ``ingest_graphiti_episode`` therefore reloads the active
artifact body from PostgreSQL at execution time and soft-skips superseded rows.

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
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

import knowledge_ingest
from knowledge_ingest import enrichment_tasks, queues
from knowledge_ingest.connector_state import FenceState
from knowledge_ingest.episode_text import MAX_TEXT_CHARS
from knowledge_ingest.routes import ingest as ingest_route

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


async def _run(
    graphiti_task,
    *,
    exists: bool,
    active: bool,
    resource_key: str | None = None,
    fence_state: FenceState = FenceState.ACTIVE,
):
    """Run the task against a stubbed pg_store; return the ingest_episode mock."""
    ingest_episode = AsyncMock(return_value="episode-uuid")
    graph_module = MagicMock()
    graph_module.ingest_episode = ingest_episode
    graph_module.EntityGraphData.return_value = SimpleNamespace()
    graph_module.flush_entity_graph_data = AsyncMock(return_value=None)

    pg_store = MagicMock()
    pg_store.read_artifact_for_enrichment = AsyncMock(
        return_value={"extra": {"document_text": "current content from PostgreSQL"}}
        if active
        else None
    )
    pg_store.artifact_exists = AsyncMock(return_value=exists)
    pg_store.artifact_is_active = AsyncMock(return_value=active)
    pg_store.update_artifact_extra = AsyncMock(return_value=None)
    pg_store.append_graphiti_episode_id = AsyncMock(return_value=None)

    # The task body does `from knowledge_ingest import pg_store` / `import graph
    # as graph_module`, which reads the PACKAGE attribute -- patching
    # sys.modules leaves the already-bound real submodule in place.
    with (
        patch.object(knowledge_ingest, "pg_store", pg_store),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _fake_conn),
        patch(
            "knowledge_ingest.connector_state.check_connector_resource_fence",
            new=AsyncMock(return_value=fence_state),
        ),
    ):
        await graphiti_task(
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
            content_type="text/markdown",
            belief_time_start=0,
            resource_key=resource_key,
        )
    return ingest_episode


async def _run_active_document(
    graphiti_task,
    document_text: str,
    episode_results: list[str | Exception | None],
    expected_error: type[Exception] | None = None,
    legacy_document_text: str | None = None,
):
    ingest_episode = AsyncMock(side_effect=episode_results)
    graph_module = MagicMock()
    graph_module.ingest_episode = ingest_episode
    graph_module.EntityGraphData.return_value = SimpleNamespace()
    graph_module.flush_entity_graph_data = AsyncMock(return_value=None)

    pg_store = MagicMock()
    pg_store.read_artifact_for_enrichment = AsyncMock(
        return_value={"extra": {"document_text": document_text}}
    )
    pg_store.artifact_exists = AsyncMock(return_value=True)
    pg_store.artifact_is_active = AsyncMock(return_value=True)
    pg_store.update_artifact_extra = AsyncMock(return_value=None)
    pg_store.append_graphiti_episode_id = AsyncMock(return_value=None)

    with (
        patch.object(knowledge_ingest, "pg_store", pg_store),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _fake_conn),
    ):
        kwargs = {
            "artifact_id": _ARTIFACT_ID,
            "org_id": _ORG_ID,
            "content_type": "text/markdown",
            "belief_time_start": 0,
            "kb_slug": "support",
            "path": "guide.md",
        }
        if legacy_document_text is not None:
            kwargs["document_text"] = legacy_document_text
        if expected_error is None:
            await graphiti_task(**kwargs)
        else:
            with pytest.raises(expected_error):
                await graphiti_task(**kwargs)
    return ingest_episode, pg_store, graph_module


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
async def test_rebuild_fences_old_graphiti_generation_but_allows_new_generation(graphiti_task):
    old_job = await _run(
        graphiti_task,
        exists=True,
        active=True,
        resource_key="connector:368884765035593759:support:connector-1:old-run",
        fence_state=FenceState.STALE_GENERATION,
    )
    new_job = await _run(
        graphiti_task,
        exists=True,
        active=True,
        resource_key="connector:368884765035593759:support:connector-1:new-run",
        fence_state=FenceState.ACTIVE,
    )

    old_job.assert_not_awaited()
    new_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_artifact_still_aborts(graphiti_task):
    """SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07 must keep holding."""
    ingest_episode = await _run(graphiti_task, exists=False, active=False)
    ingest_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_short_document_creates_one_episode_and_records_both_id_keys(graphiti_task):
    ingest_episode, pg_store, graph_module = await _run_active_document(
        graphiti_task, "One short sentence.", ["episode-1"]
    )

    ingest_episode.assert_awaited_once()
    assert ingest_episode.call_args.kwargs["document_text"] == "One short sentence."
    pg_store.append_graphiti_episode_id.assert_awaited_once_with(ANY, _ARTIFACT_ID, "episode-1")
    assert pg_store.update_artifact_extra.await_args_list[0].args[2] == {
        "graphiti_episode_part_count": 1,
        "graphiti_episode_complete": False,
    }
    assert pg_store.update_artifact_extra.await_args_list[-1].args[2] == {
        "graphiti_episode_complete": True,
        "graphiti_extraction_version": 2,
    }
    graph_module.flush_entity_graph_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_signature_document_text_is_ignored_in_favour_of_postgres(graphiti_task):
    ingest_episode, _, _ = await _run_active_document(
        graphiti_task,
        "Current body loaded from PostgreSQL.",
        ["episode-1"],
        legacy_document_text="Stale body frozen in the old queued job.",
    )

    assert ingest_episode.call_args.kwargs["document_text"] == (
        "Current body loaded from PostgreSQL."
    )


@pytest.mark.asyncio
async def test_long_document_creates_every_episode_and_records_all_ids(graphiti_task):
    paragraph = ("A complete sentence. " * 1000).strip()
    document_text = f"{paragraph}\n\n{paragraph}"
    assert len(document_text) > MAX_TEXT_CHARS

    ingest_episode, pg_store, graph_module = await _run_active_document(
        graphiti_task, document_text, ["episode-1", "episode-2"]
    )

    parts = [call.kwargs["document_text"] for call in ingest_episode.await_args_list]
    assert len(parts) == 2
    assert "\n\n".join(parts) == document_text
    assert all(len(part) <= MAX_TEXT_CHARS for part in parts)
    assert all(call.kwargs["kb_slug"] == "support" for call in ingest_episode.await_args_list)
    assert all(call.kwargs["path"] == "guide.md" for call in ingest_episode.await_args_list)
    assert [call.args[2] for call in pg_store.append_graphiti_episode_id.await_args_list] == [
        "episode-1",
        "episode-2",
    ]
    graph_module.flush_entity_graph_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_failure_records_episodes_created_before_the_error(graphiti_task):
    paragraph = ("A complete sentence. " * 1000).strip()
    document_text = f"{paragraph}\n\n{paragraph}"

    ingest_episode, pg_store, graph_module = await _run_active_document(
        graphiti_task,
        document_text,
        ["episode-1", RuntimeError("second episode failed")],
        expected_error=RuntimeError,
    )

    assert ingest_episode.await_count == 2
    pg_store.append_graphiti_episode_id.assert_awaited_once_with(ANY, _ARTIFACT_ID, "episode-1")
    assert pg_store.update_artifact_extra.await_count == 1
    graph_module.flush_entity_graph_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_episode_id_raises_and_reports_partial_coverage(graphiti_task):
    paragraph = ("A complete sentence. " * 1000).strip()

    with patch.object(enrichment_tasks.logger, "error") as log_error:
        ingest_episode, _, _ = await _run_active_document(
            graphiti_task,
            f"{paragraph}\n\n{paragraph}",
            ["episode-1", None],
            expected_error=RuntimeError,
        )

    assert ingest_episode.await_count == 2
    log_error.assert_called_once_with(
        "graphiti_episode_partial",
        artifact_id=_ARTIFACT_ID,
        org_id=_ORG_ID,
        completed_parts=1,
        expected_parts=2,
    )


@pytest.mark.asyncio
async def test_deletion_between_episode_parts_aborts_before_the_next_write(graphiti_task):
    paragraph = ("A complete sentence. " * 1000).strip()
    ingest_episode = AsyncMock(side_effect=["episode-1", "episode-2", "episode-3"])
    graph_module = MagicMock()
    graph_module.ingest_episode = ingest_episode
    graph_module.EntityGraphData.return_value = SimpleNamespace()
    graph_module.flush_entity_graph_data = AsyncMock(return_value=None)
    graph_module.delete_kb_episodes = AsyncMock(return_value=None)
    pg_store = MagicMock()
    pg_store.read_artifact_for_enrichment = AsyncMock(
        return_value={"extra": {"document_text": f"{paragraph}\n\n{paragraph}\n\n{paragraph}"}}
    )
    pg_store.artifact_exists = AsyncMock(side_effect=[True, True, False])
    pg_store.artifact_is_active = AsyncMock(return_value=True)
    pg_store.update_artifact_extra = AsyncMock(return_value=None)
    pg_store.append_graphiti_episode_id = AsyncMock(return_value=None)

    with (
        patch.object(knowledge_ingest, "pg_store", pg_store),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _fake_conn),
    ):
        await graphiti_task(
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
            content_type="text/markdown",
            belief_time_start=0,
            kb_slug="support",
            path="guide.md",
        )

    assert ingest_episode.await_count == 2
    graph_module.delete_kb_episodes.assert_awaited_once_with(_ORG_ID, ["episode-2"])
    assert pg_store.artifact_exists.await_count == 3


@pytest.mark.asyncio
async def test_legacy_background_writer_records_both_id_keys():
    conn = SimpleNamespace()

    with (
        patch.object(
            ingest_route.graph_module,
            "ingest_episode",
            new=AsyncMock(return_value="episode-1"),
        ),
        patch.object(
            ingest_route.pg_store,
            "update_artifact_extra",
            new=AsyncMock(return_value=None),
        ) as update_extra,
    ):
        await ingest_route._graphiti_background(
            conn,
            _ARTIFACT_ID,
            "One short sentence.",
            _ORG_ID,
            "text/markdown",
            0,
        )

    assert update_extra.await_args.args[2] == {
        "graphiti_episode_id": "episode-1",
        "graphiti_episode_ids": ["episode-1"],
    }
