"""Every operation that rewrites a knowledge base starts only with the owner's
approval marker, previews by default and cannot run twice at once on the same
knowledge base (AGENTS.md: production knowledge data is rewritten only with
approval per run)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import operations, rebuild_tasks
from knowledge_ingest.scripts import kb_patch, sparse_backfill

SERVICE_ROOT = Path(__file__).resolve().parent.parent

# Module plus its arguments, ending in the flag that makes the run write.
WRITING_OPERATIONS = [
    ("knowledge_ingest.scripts.kb_patch", ["--org", "o", "--execute"]),
    ("knowledge_ingest.scripts.sparse_backfill", ["--org", "o", "--execute"]),
    ("knowledge_ingest.scripts.requeue_failed_artifacts", ["--execute"]),
    ("knowledge_ingest.scripts.repair_pg_qdrant_drift", ["a1", "--execute"]),
    ("knowledge_ingest.scripts.backfill_crawl_source_identity", ["--apply"]),
    ("knowledge_ingest.scripts.migrate_collection", ["--execute"]),
    ("knowledge_ingest.backfill", ["--org-id", "o", "--execute"]),
    ("knowledge_ingest.backfill_tasks", ["--org-id", "o", "--kb", "k", "--execute"]),
    ("knowledge_ingest.rebuild_tasks", ["--org", "o", "--kb", "k", "--execute"]),
    ("scripts.backfill_docling_parent_chunks", ["--execute"]),
    ("scripts.backfill_document_titles", ["--org-id", "o", "--execute"]),
    ("scripts.backfill_entity_names", ["--org-id", "o", "--execute"]),
    ("scripts.backfill_episode_document_names", ["--org-id", "o", "--execute"]),
    ("scripts.backfill_graphiti_episode_ids", ["--org-id", "o", "--execute"]),
    ("scripts.prune_stale_graph", ["--org", "o", "--execute"]),
    ("scripts.purge_deleted_documents", ["--org", "o", "--execute"]),
    ("scripts.reextract_document_graph", ["--org", "o", "--artifact", "a", "--execute"]),
]


@pytest.mark.parametrize(("module", "args"), WRITING_OPERATIONS)
def test_write_run_without_the_approval_marker_is_refused_before_any_work(module, args):
    env = {k: v for k, v in os.environ.items() if k != "INGEST_OK"}
    run = subprocess.run(  # noqa: S603 - fixed argv of this repo's own modules
        [sys.executable, "-m", module, *args],
        cwd=SERVICE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 2, run.stderr[-400:]
    assert "INGEST_OK=1" in run.stderr


def test_the_marker_lets_a_write_run_start(monkeypatch):
    monkeypatch.setenv("INGEST_OK", "1")
    operations.require_ingest_ok("op")


def _pool_whose_lock_is(granted: bool):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=granted)
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


async def test_a_run_for_a_knowledge_base_in_use_is_refused():
    pool, _ = _pool_whose_lock_is(False)
    with (
        patch.object(operations, "get_pool", AsyncMock(return_value=pool)),
        pytest.raises(operations.OperationBusy),
    ):
        async with operations.kb_operation("org-1", ["kb-1"], execute=True):
            raise AssertionError("must not start")


async def test_the_lock_is_released_when_the_operation_ends():
    pool, conn = _pool_whose_lock_is(True)
    with patch.object(operations, "get_pool", AsyncMock(return_value=pool)):
        async with operations.kb_operation("org-1", ["kb-1"], execute=True):
            pass
    assert "pg_advisory_unlock" in conn.execute.await_args.args[0]


async def test_a_preview_takes_no_lock():
    with patch.object(operations, "get_pool", AsyncMock(side_effect=AssertionError)):
        async with operations.kb_operation("org-1", ["kb-1"], execute=False):
            pass


ARTIFACTS = [
    {"id": "a1", "path": "p1", "extra": {"document_text": "# Title\n\nBody text."}},
    {"id": "a2", "path": "p2", "extra": {}},
]


async def test_rebuild_previews_by_default_and_changes_nothing():
    enrich = AsyncMock()
    with (
        patch.object(rebuild_tasks, "_list_active_artifacts", AsyncMock(return_value=ARTIFACTS)),
        patch("knowledge_ingest.enrichment_tasks._enrich_document", enrich),
    ):
        result = await rebuild_tasks.rebuild_kb_inline("org-1", "kb-1")

    assert result["dry_run"] is True
    assert result["artifacts"] == 2
    assert result["without_stored_text"] == 1
    assert result["first"][0]["path"] == "p1"
    enrich.assert_not_awaited()


async def test_rebuild_execute_without_the_marker_is_refused(monkeypatch):
    monkeypatch.delenv("INGEST_OK", raising=False)
    listing = AsyncMock(return_value=ARTIFACTS)
    with (
        patch.object(rebuild_tasks, "_list_active_artifacts", listing),
        pytest.raises(operations.ApprovalRequired),
    ):
        await rebuild_tasks.rebuild_kb_inline("org-1", "kb-1", execute=True)
    listing.assert_not_awaited()


async def test_the_queued_rebuild_task_needs_the_marker_too(monkeypatch):
    monkeypatch.delenv("INGEST_OK", raising=False)
    listing = AsyncMock(return_value=ARTIFACTS)
    with (
        patch.object(rebuild_tasks, "_list_active_artifacts", listing),
        pytest.raises(operations.ApprovalRequired),
    ):
        await rebuild_tasks._rebuild_kb_core("org-1", "kb-1", execute=True)
    listing.assert_not_awaited()


async def test_a_second_rebuild_of_the_same_knowledge_base_is_refused(monkeypatch):
    monkeypatch.setenv("INGEST_OK", "1")
    pool, _ = _pool_whose_lock_is(False)
    listing = AsyncMock(return_value=ARTIFACTS)
    with (
        patch.object(operations, "get_pool", AsyncMock(return_value=pool)),
        patch.object(rebuild_tasks, "_list_active_artifacts", listing),
        pytest.raises(operations.OperationBusy),
    ):
        await rebuild_tasks._rebuild_kb_core("org-1", "kb-1", execute=True)
    listing.assert_not_awaited()


async def test_a_patch_of_a_knowledge_base_in_use_is_refused():
    pool, _ = _pool_whose_lock_is(False)
    patch_kb = AsyncMock()
    with (
        patch.object(operations, "get_pool", AsyncMock(return_value=pool)),
        patch.object(kb_patch.kb_config, "org_kb_settings", AsyncMock(return_value={"kb-1": ()})),
        patch.object(kb_patch, "_patch_kb", patch_kb),
        pytest.raises(operations.OperationBusy),
    ):
        await kb_patch.patch_org(MagicMock(), "org-1", "kb-1", execute=True)
    patch_kb.assert_not_awaited()


async def test_a_sparse_backfill_of_a_knowledge_base_in_use_is_refused():
    pool, _ = _pool_whose_lock_is(False)
    backfill = AsyncMock()
    args = argparse.Namespace(org="org-1", kb="kb-1", execute=True, batch_size=None)
    with (
        patch.object(operations, "get_pool", AsyncMock(return_value=pool)),
        patch.object(sparse_backfill, "get_client", MagicMock()),
        patch.object(sparse_backfill, "count_chunks", AsyncMock(return_value={})),
        patch.object(sparse_backfill, "backfill_sparse", backfill),
        pytest.raises(operations.OperationBusy),
    ):
        await sparse_backfill._main(args)
    backfill.assert_not_awaited()


def test_a_preview_needs_no_marker(monkeypatch):
    monkeypatch.delenv("INGEST_OK", raising=False)
    operations.guard_cli("op", execute=False)
