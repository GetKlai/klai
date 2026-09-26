from __future__ import annotations

import copy
import hashlib
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

import knowledge_ingest
from knowledge_ingest import enrichment_tasks, pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.graph import EntityGraphData
from knowledge_ingest.models import IngestRequest
from knowledge_ingest.routes import ingest as ingest_route
from knowledge_ingest.routes import kb_sources

_ARTIFACT_ID = "0f9c1a2b-3d4e-4f50-9a61-72b83c94d5e6"
_ORG_ID = "org-graph-refresh"
_KB_SLUG = "support"
_PATH = "guides/page.md"
# A document whose graph was built under an older extraction version.
_LEGACY_GRAPH = {"graphiti_episode_ids": ["episode-old"]}


class _TaskHandle:
    def __init__(self) -> None:
        self.configure = MagicMock(return_value=self)
        self.defer_async = AsyncMock(return_value=None)


class _ProcApp:
    def __init__(self) -> None:
        self.ingest_graphiti_episode = _TaskHandle()
        self.enrich_document_interactive = _TaskHandle()


class _CapturingApp:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}

    def task(self, **_kwargs):
        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            return fn

        return _decorator


def _request(
    content: str = "# Calling guide\n\nKlai routes calls to the right colleague.",
) -> IngestRequest:
    return IngestRequest(
        org_id=_ORG_ID,
        kb_slug=_KB_SLUG,
        path=_PATH,
        content=content,
        source_type="docs",
        content_type="kb_article",
    )


def _artifact_state(req: IngestRequest, extra: dict) -> dict:
    return {
        "id": _ARTIFACT_ID,
        "content_hash": hashlib.sha256(req.content.encode()).hexdigest(),
        "extra": extra,
        "belief_time_start": 1_755_820_800,
        "content_type": "stored/type",
    }


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=None)
    return conn


@asynccontextmanager
async def _tenant_connection(_org_id: str):
    yield SimpleNamespace()


async def _ingest_unchanged(
    req: IngestRequest, *, extra: dict, app: _ProcApp, slot_free: bool = True
) -> dict:
    state = _artifact_state(req, extra)
    conn = _mock_conn()
    with (
        patch.object(pg_store, "reserve_graph_refresh_slot", AsyncMock(return_value=slot_free)),
        patch.object(
            pg_store,
            "get_active_artifact_state",
            AsyncMock(return_value=state),
            create=True,
        ),
        patch.object(settings, "graphiti_enabled", True),
        patch("knowledge_ingest.enrichment_tasks.get_app", return_value=app),
    ):
        return await ingest_route.ingest_document(conn, req)


@pytest.mark.asyncio
async def test_unchanged_content_with_legacy_graph_rules_queues_replacement() -> None:
    req = _request()
    app = _ProcApp()

    result = await _ingest_unchanged(req, extra={}, app=app)

    assert result == {
        "status": "skipped",
        "reason": "content unchanged",
        "chunks": 0,
        "graph_refresh": "queued",
    }
    app.ingest_graphiti_episode.defer_async.assert_awaited_once_with(
        artifact_id=_ARTIFACT_ID,
        org_id=_ORG_ID,
        content_type="kb_article",
        belief_time_start=1_755_820_800,
        kb_slug=_KB_SLUG,
        path=_PATH,
        replace_stale=True,
    )
    assert req.content not in repr(app.ingest_graphiti_episode.defer_async.await_args.kwargs)


@pytest.mark.asyncio
async def test_unchanged_content_with_current_graph_rules_does_not_refresh() -> None:
    req = _request()
    app = _ProcApp()

    result = await _ingest_unchanged(
        req,
        extra={"graphiti_extraction_version": 2},
        app=app,
    )

    assert result == {"status": "skipped", "reason": "content unchanged", "chunks": 0}
    app.ingest_graphiti_episode.defer_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_navigation_page_replaces_history_with_current_skip_marker() -> None:
    content = "\n".join(f"* [Page {number}](https://example.test/{number})" for number in range(8))
    req = _request(content)
    app = _ProcApp()
    stale_ids = ["episode-old-1", "episode-old-2"]

    with (
        patch.object(
            pg_store,
            "get_episode_ids_for_document_history",
            AsyncMock(return_value=stale_ids),
        ) as get_history,
        patch.object(pg_store, "update_artifact_extra", AsyncMock()) as update_extra,
        patch("knowledge_ingest.graph.delete_kb_episodes", AsyncMock()) as delete_episodes,
    ):
        result = await _ingest_unchanged(req, extra={}, app=app)

    assert result["graph_refresh"] == "skipped:navigation_page"
    get_history.assert_awaited_once_with(ANY, _ORG_ID, [_ARTIFACT_ID])
    delete_episodes.assert_awaited_once_with(_ORG_ID, stale_ids)
    update_extra.assert_awaited_once_with(
        ANY,
        _ARTIFACT_ID,
        {
            "graphiti_episode_ids": [],
            "graphiti_episode_part_count": 0,
            "graphiti_episode_complete": True,
            "graphiti_episode_id": "skipped:navigation_page",
            "graphiti_extraction_version": 2,
        },
    )
    app.ingest_graphiti_episode.defer_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "expected_graph_jobs"),
    [(_LEGACY_GRAPH, 1), ({**_LEGACY_GRAPH, "graphiti_extraction_version": 2}, 0)],
    ids=["legacy-graph-rules", "current-graph-rules"],
)
async def test_upload_reindex_always_enriches_and_only_refreshes_legacy_graph(
    extra: dict,
    expected_graph_jobs: int,
) -> None:
    app = _ProcApp()
    row = {
        "artifact_id": _ARTIFACT_ID,
        "org_id": _ORG_ID,
        "kb_slug": _KB_SLUG,
        "path": _PATH,
        "content_type": "application/pdf",
        "belief_time_start": 1_755_820_800,
        "extra": {"document_text": "Klai routes calls to the right colleague.", **extra},
    }
    with (
        patch.object(
            kb_sources,
            "assert_caller_identity_tenant_only",
            AsyncMock(return_value=_ORG_ID),
        ),
        patch.object(kb_sources, "tenant_scoped_connection", _tenant_connection),
        patch.object(
            pg_store,
            "set_artifact_index_status",
            AsyncMock(return_value={"artifact_id": _ARTIFACT_ID, "path": _PATH}),
        ),
        patch.object(pg_store, "read_artifact_for_enrichment", AsyncMock(return_value=row)),
        patch.object(settings, "graphiti_enabled", True),
        patch.object(enrichment_tasks, "get_app", return_value=app),
    ):
        response = await kb_sources.reindex_upload(
            MagicMock(),
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
        )

    assert response.index_status == "pending"
    assert app.enrich_document_interactive.defer_async.await_count == 1
    assert app.ingest_graphiti_episode.defer_async.await_count == expected_graph_jobs
    if expected_graph_jobs:
        assert app.ingest_graphiti_episode.defer_async.await_args.kwargs["replace_stale"] is True


@pytest.mark.asyncio
async def test_graph_refresh_failure_does_not_fail_the_unchanged_content_skip() -> None:
    """A FalkorDB/queue hiccup in the refresh must not 500 a previously
    side-effect-free "content unchanged" ingest."""
    content = "\n".join(f"* [Page {number}](https://example.test/{number})" for number in range(8))
    req = _request(content)
    app = _ProcApp()

    with (
        patch.object(
            pg_store,
            "get_episode_ids_for_document_history",
            AsyncMock(return_value=["episode-old"]),
        ),
        patch(
            "knowledge_ingest.graph.delete_kb_episodes",
            AsyncMock(side_effect=RuntimeError("falkordb down")),
        ),
    ):
        result = await _ingest_unchanged(req, extra={}, app=app)

    assert result == {"status": "skipped", "reason": "content unchanged", "chunks": 0}


@pytest.mark.asyncio
async def test_refresh_of_job_already_waiting_in_queue_reports_already_queued() -> None:
    from procrastinate.exceptions import AlreadyEnqueued

    req = _request()
    app = _ProcApp()
    app.ingest_graphiti_episode.defer_async = AsyncMock(side_effect=AlreadyEnqueued())

    result = await _ingest_unchanged(req, extra=_LEGACY_GRAPH, app=app)

    assert result["graph_refresh"] == "already_queued"


@pytest.mark.asyncio
async def test_refresh_job_serialises_against_a_running_extraction_via_lock() -> None:
    """queueing_lock dedups only todo jobs; the execution lock is what stops a
    refresh from running concurrently with an in-flight extraction and deleting
    the episodes it is appending."""
    req = _request()
    app = _ProcApp()

    await _ingest_unchanged(req, extra=_LEGACY_GRAPH, app=app)

    assert app.ingest_graphiti_episode.configure.call_args.kwargs == {
        "lock": f"graphiti:{_ARTIFACT_ID}",
        "queueing_lock": f"graphiti:{_ARTIFACT_ID}",
    }


@pytest.fixture
def graphiti_task() -> object:
    app = _CapturingApp()
    enrichment_tasks._register_tasks(app)
    return app.tasks["ingest_graphiti_episode"]


async def _run_graphiti_task(graphiti_task: object, *, replace_stale: bool = False):
    events: list[str] = []
    graph_module = MagicMock()
    graph_module.EntityGraphData.return_value = SimpleNamespace()
    graph_module.delete_kb_episodes = AsyncMock(side_effect=lambda *_args: events.append("delete"))
    graph_module.ingest_episode = AsyncMock(
        side_effect=lambda **_kwargs: events.append("ingest") or "episode-new"
    )
    graph_module.flush_entity_graph_data = AsyncMock(return_value=None)

    store = MagicMock()
    store.read_artifact_for_enrichment = AsyncMock(
        return_value={"extra": {"document_text": "Klai routes calls to the right colleague."}}
    )
    store.artifact_exists = AsyncMock(return_value=True)
    store.artifact_is_active = AsyncMock(return_value=True)
    store.get_episode_ids_for_document_history = AsyncMock(
        side_effect=lambda *_args: events.append("history") or ["episode-old"]
    )

    async def _update(_conn, _artifact_id, values):
        if values == {
            "graphiti_episode_ids": [],
            "graphiti_episode_id": None,
            "graphiti_episode_complete": False,
            "graphiti_episode_part_count": 0,
        }:
            events.append("reset")

    store.update_artifact_extra = AsyncMock(side_effect=_update)
    store.append_graphiti_episode_id = AsyncMock(return_value=None)

    with (
        patch.object(knowledge_ingest, "pg_store", store),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _tenant_connection),
    ):
        await graphiti_task(
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
            content_type="kb_article",
            belief_time_start=1_755_820_800,
            kb_slug=_KB_SLUG,
            path=_PATH,
            replace_stale=replace_stale,
        )
    return events, store, graph_module


@pytest.mark.asyncio
async def test_replacement_run_deletes_and_resets_history_before_new_extraction(
    graphiti_task,
) -> None:
    events, store, graph_module = await _run_graphiti_task(graphiti_task, replace_stale=True)

    assert events[:4] == ["history", "delete", "reset", "ingest"]
    graph_module.delete_kb_episodes.assert_awaited_once_with(_ORG_ID, ["episode-old"])
    assert store.update_artifact_extra.await_args_list[-1] == call(
        ANY,
        _ARTIFACT_ID,
        {"graphiti_episode_complete": True, "graphiti_extraction_version": 2},
    )


@pytest.mark.asyncio
async def test_normal_graph_run_stamps_current_extraction_version(graphiti_task) -> None:
    _events, store, graph_module = await _run_graphiti_task(graphiti_task)

    graph_module.delete_kb_episodes.assert_not_awaited()
    store.get_episode_ids_for_document_history.assert_not_awaited()
    assert store.update_artifact_extra.await_args_list[-1] == call(
        ANY,
        _ARTIFACT_ID,
        {"graphiti_episode_complete": True, "graphiti_extraction_version": 2},
    )


@pytest.mark.asyncio
async def test_version_only_refresh_beyond_the_daily_cap_is_not_enqueued() -> None:
    app = _ProcApp()

    result = await _ingest_unchanged(_request(), extra=_LEGACY_GRAPH, app=app, slot_free=False)

    assert result["graph_refresh"] == "deferred:daily_cap"
    app.ingest_graphiti_episode.defer_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_slot_is_given_back_when_the_enqueue_fails() -> None:
    app = _ProcApp()
    app.ingest_graphiti_episode.defer_async = AsyncMock(side_effect=RuntimeError("queue down"))

    with patch.object(pg_store, "release_graph_refresh_slot", AsyncMock()) as release:
        result = await _ingest_unchanged(_request(), extra=_LEGACY_GRAPH, app=app)

    assert "graph_refresh" not in result
    release.assert_awaited_once_with(ANY, _ORG_ID)


@pytest.mark.asyncio
async def test_user_reindex_refreshes_the_graph_past_the_cap_and_cool_off() -> None:
    app = _ProcApp()
    row = {
        "artifact_id": _ARTIFACT_ID,
        "org_id": _ORG_ID,
        "kb_slug": _KB_SLUG,
        "path": _PATH,
        "content_type": "application/pdf",
        "belief_time_start": 1_755_820_800,
        "extra": {
            "document_text": "Klai routes calls to the right colleague.",
            "graphiti_exhausted_at": int(time.time()),
        },
    }
    with (
        patch.object(
            kb_sources, "assert_caller_identity_tenant_only", AsyncMock(return_value=_ORG_ID)
        ),
        patch.object(kb_sources, "tenant_scoped_connection", _tenant_connection),
        patch.object(
            pg_store,
            "set_artifact_index_status",
            AsyncMock(return_value={"artifact_id": _ARTIFACT_ID, "path": _PATH}),
        ),
        patch.object(pg_store, "read_artifact_for_enrichment", AsyncMock(return_value=row)),
        patch.object(settings, "graphiti_enabled", True),
        patch.object(enrichment_tasks, "get_app", return_value=app),
        patch.object(pg_store, "reserve_graph_refresh_slot", AsyncMock(return_value=False)),
    ):
        response = await kb_sources.reindex_upload(
            MagicMock(), artifact_id=_ARTIFACT_ID, org_id=_ORG_ID
        )

    assert response.graph_refresh == "queued"
    app.ingest_graphiti_episode.defer_async.assert_awaited_once()


class _ArtifactRow:
    """pg_store double that merges extra patches the way the JSONB update does."""

    def __init__(self, extra: dict, predecessor_ids: tuple[str, ...] = ()) -> None:
        self.extra = {"document_text": "Klai routes calls.", **copy.deepcopy(extra)}
        self.predecessor_ids = list(predecessor_ids)

    async def read_artifact_for_enrichment(self, _conn, _artifact_id):
        return {"extra": copy.deepcopy(self.extra)}

    async def artifact_exists(self, *_args):
        return True

    async def artifact_is_active(self, *_args):
        return True

    async def get_episode_ids_for_document_history(self, *_args):
        return self.predecessor_ids + list(self.extra.get("graphiti_episode_ids", []))

    async def update_artifact_extra(self, _conn, _artifact_id, values):
        self.extra.update(copy.deepcopy(values))

    async def append_graphiti_episode_id(self, _conn, _artifact_id, episode_id):
        ids = self.extra.setdefault("graphiti_episode_ids", [])
        if episode_id not in ids:
            ids.append(episode_id)


async def _run_two_part_job(
    graphiti_task,
    row: _ArtifactRow,
    episode_results: list[str | None],
    *,
    attempts: int = 0,
    replace_stale: bool = False,
) -> MagicMock:
    graph_module = MagicMock()
    graph_module.EntityGraphData = EntityGraphData
    graph_module.ingest_episode = AsyncMock(side_effect=episode_results)
    graph_module.delete_kb_episodes = AsyncMock()
    graph_module.flush_entity_graph_data = AsyncMock()
    graph_module.load_entity_graph_data = AsyncMock()
    with (
        patch.object(knowledge_ingest, "pg_store", row),
        patch.object(knowledge_ingest, "graph", graph_module),
        patch.object(enrichment_tasks, "tenant_scoped_connection", _tenant_connection),
        patch.object(enrichment_tasks, "split_episode_text", return_value=["part 1", "part 2"]),
    ):
        await graphiti_task(
            SimpleNamespace(job=SimpleNamespace(attempts=attempts)),
            artifact_id=_ARTIFACT_ID,
            org_id=_ORG_ID,
            content_type="kb_article",
            belief_time_start=1_755_820_800,
            kb_slug=_KB_SLUG,
            path=_PATH,
            replace_stale=replace_stale,
        )
    return graph_module


@pytest.mark.asyncio
async def test_second_part_extraction_gets_only_the_first_part_as_context(graphiti_task) -> None:
    graph_module = await _run_two_part_job(
        graphiti_task, _ArtifactRow({}), ["episode-1", "episode-2"]
    )

    assert [
        c.kwargs["previous_episode_id"] for c in graph_module.ingest_episode.await_args_list
    ] == [
        None,
        "episode-1",
    ]


@pytest.mark.asyncio
async def test_retried_multi_part_job_does_not_rerun_a_completed_part(graphiti_task) -> None:
    row = _ArtifactRow(_LEGACY_GRAPH)
    with pytest.raises(RuntimeError):
        await _run_two_part_job(graphiti_task, row, ["episode-1", None], replace_stale=True)

    graph_module = await _run_two_part_job(
        graphiti_task, row, ["episode-2"], attempts=1, replace_stale=True
    )

    assert [c.kwargs["document_text"] for c in graph_module.ingest_episode.await_args_list] == [
        "part 2"
    ]
    assert graph_module.ingest_episode.await_args.kwargs["previous_episode_id"] == "episode-1"
    graph_module.delete_kb_episodes.assert_not_awaited()
    graph_module.load_entity_graph_data.assert_awaited_once_with(_ORG_ID, ["episode-1"], ANY)
    assert row.extra["graphiti_episode_ids"] == ["episode-1", "episode-2"]
    assert row.extra["graphiti_episode_complete"] is True


@pytest.mark.asyncio
async def test_resumed_replacement_deletes_only_episodes_of_earlier_versions(graphiti_task) -> None:
    row = _ArtifactRow(
        {
            "graphiti_episode_ids": ["episode-1"],
            "graphiti_episode_ids_version": 2,
            "graphiti_episode_complete": False,
        },
        predecessor_ids=("episode-predecessor",),
    )

    graph_module = await _run_two_part_job(
        graphiti_task, row, ["episode-2"], attempts=1, replace_stale=True
    )

    graph_module.delete_kb_episodes.assert_awaited_once_with(_ORG_ID, ["episode-predecessor"])
    assert row.extra["graphiti_episode_ids"] == ["episode-1", "episode-2"]


@pytest.mark.asyncio
async def test_exhausted_graph_job_waits_out_the_cool_off_but_a_change_extracts_at_once(
    graphiti_task,
) -> None:
    from tests.test_renamed_connector_page_supersedes_by_source_ref import (
        _ingest_patches,
        _make_conn,
    )

    row = _ArtifactRow({})
    with pytest.raises(RuntimeError):
        await _run_two_part_job(
            graphiti_task,
            row,
            ["episode-1", None],
            attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS,
        )
    unchanged = _request()
    app = _ProcApp()

    result = await _ingest_unchanged(unchanged, extra=row.extra, app=app)

    assert result["graph_refresh"] == "deferred:cool_off"
    app.ingest_graphiti_episode.defer_async.assert_not_awaited()

    cooled_off = {**row.extra, "graphiti_exhausted_at": int(time.time()) - 8 * 86_400}
    result = await _ingest_unchanged(unchanged, extra=cooled_off, app=app)

    assert result["graph_refresh"] == "queued"

    changed = _request(unchanged.content + "\n\nKlai now also routes chat messages.")
    with (
        _ingest_patches(changed, closed_rows=[]),
        patch.object(
            pg_store,
            "get_active_artifact_state",
            AsyncMock(return_value=_artifact_state(unchanged, row.extra)),
        ),
    ):
        await ingest_route.ingest_document(_make_conn(), changed)
        changed_app = enrichment_tasks.get_app()

    changed_app.ingest_graphiti_episode.configure.return_value.defer_async.assert_awaited_once()
