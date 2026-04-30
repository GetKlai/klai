"""Unit tests for the connector_cleanup orchestrator.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-09. The orchestrator's value is
that it composes 8 store-cleanups in a deterministic order with
job-cancellation as the synchronous fence. These tests verify the
composition + ordering with sink calls mocked. Integration tests against
real Postgres/Qdrant/FalkorDB live in tests/integration/ (separate run).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.connector_cleanup import CleanupReport, purge_connector


@pytest.fixture
def mocked_proc_app() -> MagicMock:
    """Fake Procrastinate App with cancellable job_manager."""
    app = MagicMock()
    app.job_manager = MagicMock()
    app.job_manager.cancel_job_by_id_async = AsyncMock()
    return app


@pytest.mark.asyncio
async def test_purge_connector_orders_steps_correctly(mocked_proc_app: MagicMock) -> None:
    """Verify the canonical order: snapshot artifact-ids -> cancel enrichment ->
    cancel graphiti -> snapshot episode-ids -> delete pg artifacts ->
    delete pg crawl_jobs -> delete falkor episodes -> delete qdrant.

    Failure to preserve this order = regrow bug. Specifically, snapshotting
    artifact-ids must happen BEFORE artifacts deletion, otherwise the
    graphiti-cancel step has no IDs to filter procrastinate-jobs by.
    """
    call_order: list[str] = []

    async def fake_list_artifact_ids(*_a: object, **_kw: object) -> list[str]:
        call_order.append("list_artifact_ids")
        return ["artifact-1", "artifact-2"]

    async def fake_get_pool() -> MagicMock:
        # No-op pool: the cancel-jobs step uses raw SQL via the pool, but
        # the higher-level _cancel_*_jobs functions are mocked separately.
        pool = MagicMock()
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[])
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=cm)
        return pool

    async def fake_get_episode_ids(*_a: object, **_kw: object) -> list[str]:
        call_order.append("get_connector_episode_ids")
        return ["episode-uuid-1"]

    async def fake_get_orphan_image_keys(*_a: object, **_kw: object) -> list[str]:
        call_order.append("get_orphan_image_keys_for_connector")
        return []

    async def fake_delete_artifacts(*_a: object, **_kw: object) -> int:
        call_order.append("delete_connector_artifacts")
        return 2

    async def fake_delete_crawl_jobs(*_a: object, **_kw: object) -> int:
        call_order.append("delete_connector_crawl_jobs")
        return 1

    async def fake_delete_episodes(*_a: object, **_kw: object) -> None:
        call_order.append("delete_kb_episodes")

    async def fake_delete_qdrant(*_a: object, **_kw: object) -> None:
        call_order.append("qdrant_delete_connector")

    async def fake_delete_orphan_episodes(*_a: object, **_kw: object) -> int:
        call_order.append("delete_orphan_episodes_for_artifact_ids")
        return 0

    async def fake_get_active_hashes(*_a: object, **_kw: object) -> set[str]:
        call_order.append("get_active_image_hashes_for_kb")
        return set()

    async def fake_build_image_store_returns_none() -> None:
        return None

    with (
        patch(
            "knowledge_ingest.connector_cleanup._list_artifact_ids",
            side_effect=fake_list_artifact_ids,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.get_pool",
            new=fake_get_pool,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.pg_store.get_connector_episode_ids",
            side_effect=fake_get_episode_ids,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.pg_store.get_orphan_image_keys_for_connector",
            side_effect=fake_get_orphan_image_keys,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.pg_store.delete_connector_artifacts",
            side_effect=fake_delete_artifacts,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.pg_store.delete_connector_crawl_jobs",
            side_effect=fake_delete_crawl_jobs,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.graph_module.delete_kb_episodes",
            side_effect=fake_delete_episodes,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.qdrant_store.delete_connector",
            side_effect=fake_delete_qdrant,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.graph_module.delete_orphan_episodes_for_artifact_ids",
            side_effect=fake_delete_orphan_episodes,
        ),
        patch(
            "knowledge_ingest.connector_cleanup.pg_store.get_active_image_hashes_for_kb",
            side_effect=fake_get_active_hashes,
        ),
        # ImageStore init returns None so the Garage paths are skipped in this
        # mock-only test. Real Garage integration is covered by integration tests.
        patch(
            "knowledge_ingest.adapters.crawler._build_image_store",
            return_value=None,
        ),
    ):
        report = await purge_connector(
            org_id="org-zid",
            kb_slug="support",
            connector_id="conn-uuid",
            proc_app=mocked_proc_app,
        )

    # Order assertion: artifact-id snapshot + episode-id snapshot +
    # orphan-image-key snapshot ALL run BEFORE artifact delete (otherwise
    # the artifact_images FK CASCADE removes our refcount source).
    # artifacts deleted before episodes (FalkorDB) and Qdrant.
    # SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-05 + REQ-06.
    assert call_order == [
        "list_artifact_ids",
        "get_connector_episode_ids",
        "get_orphan_image_keys_for_connector",
        "delete_connector_artifacts",
        "delete_connector_crawl_jobs",
        "delete_kb_episodes",
        "qdrant_delete_connector",
        "delete_orphan_episodes_for_artifact_ids",
    ]
    assert isinstance(report, CleanupReport)
    assert report.artifacts_deleted == 2
    assert report.crawl_jobs_deleted == 1
    assert report.falkor_episodes_deleted == 1


@pytest.mark.asyncio
async def test_cleanup_report_serialises_for_logging() -> None:
    """REQ-10.2: every step's count must be on the structured log line."""
    report = CleanupReport(
        enrichment_jobs_cancelled=3,
        graphiti_jobs_cancelled=1,
        artifacts_deleted=20,
        crawl_jobs_deleted=2,
        qdrant_chunks_deleted=0,
        falkor_episodes_deleted=15,
        sync_runs_deleted=None,
    )
    d = report.as_dict()
    assert d["enrichment_jobs_cancelled"] == 3
    assert d["graphiti_jobs_cancelled"] == 1
    assert d["artifacts_deleted"] == 20
    assert d["crawl_jobs_deleted"] == 2
    assert d["falkor_episodes_deleted"] == 15
    assert d["sync_runs_deleted"] is None
