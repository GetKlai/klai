"""The graph cannot heal itself, so the backfill must be correct.

SPEC-RAG-GRAPH-CITE-002 renames episodes at ingest, but knowledge-ingest
dedups on content_hash: re-crawling an unchanged page never produces a new
episode. Every fact extracted before the change therefore keeps a pointer to
a superseded artifact version unless this backfill runs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import graph as graph_module


def _row(kb_slug, path, extra):
    return {"kb_slug": kb_slug, "path": path, "extra": json.dumps(extra) if extra else None}


class TestCollectRenames:
    @pytest.mark.asyncio
    async def test_groups_every_version_of_a_document_under_one_name(self):
        """Several versions share a document, and all their episodes must move.

        Superseded artifacts are the point of the exercise: their episodes
        carry the stale edges whose citations do not resolve.
        """
        from scripts.backfill_episode_document_names import collect_renames

        rows = [
            _row("support", "webphone", {"graphiti_episode_id": "ep-v1"}),
            _row("support", "webphone", {"graphiti_episode_id": "ep-v2"}),
            _row("support", "yealink", {"graphiti_episode_id": "ep-y"}),
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "scripts.backfill_episode_document_names.tenant_scoped_connection", return_value=ctx
        ):
            renames, skipped = await collect_renames("org-1")

        assert renames == {
            "doc:support:webphone": ["ep-v1", "ep-v2"],
            "doc:support:yealink": ["ep-y"],
        }
        assert skipped == 0

    @pytest.mark.asyncio
    async def test_skips_artifacts_without_a_usable_episode_pointer(self):
        """no-chunks is backfill.py's sentinel, not an episode uuid."""
        from scripts.backfill_episode_document_names import collect_renames

        rows = [
            _row("support", "a", {"graphiti_episode_id": "no-chunks"}),
            _row("support", "b", {}),
            _row("support", "c", None),
            _row("", "d", {"graphiti_episode_id": "ep-1"}),
            _row("support", "", {"graphiti_episode_id": "ep-2"}),
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "scripts.backfill_episode_document_names.tenant_scoped_connection", return_value=ctx
        ):
            renames, skipped = await collect_renames("org-1")

        assert renames == {}
        assert skipped == 5


class TestRenameEpisodes:
    @pytest.mark.asyncio
    async def test_renames_within_the_tenant_graph(self):
        """TENANT ISOLATION: the write goes to the org's own FalkorDB database."""
        driver = MagicMock()
        driver.execute_query = AsyncMock(return_value=([{"n": 2}], None, None))
        graphiti = MagicMock()
        graphiti.driver.clone = MagicMock(return_value=driver)

        with (
            patch.object(graph_module, "_get_graphiti", return_value=graphiti),
            patch.object(graph_module.settings, "graphiti_enabled", True),
        ):
            renamed = await graph_module.rename_episodes_to_document_keys(
                "org-1", {"doc:support:webphone": ["ep-1", "ep-2"]}
            )

        graphiti.driver.clone.assert_called_once_with("org-1")
        assert renamed == 2
        kwargs = driver.execute_query.await_args.kwargs
        assert kwargs["uuids"] == ["ep-1", "ep-2"]
        assert kwargs["name"] == "doc:support:webphone"

    @pytest.mark.asyncio
    async def test_no_op_when_there_is_nothing_to_rename(self):
        graphiti = MagicMock()
        with patch.object(graph_module, "_get_graphiti", return_value=graphiti):
            assert await graph_module.rename_episodes_to_document_keys("org-1", {}) == 0
        graphiti.driver.clone.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_a_metadata_rename_not_a_reingest(self):
        """Guard the property that makes this safe to run on a live tenant.

        A re-ingest would cost ~5 LLM calls per episode out of the shared
        klai-fast budget. This must only ever touch the episode name.
        """
        driver = MagicMock()
        driver.execute_query = AsyncMock(return_value=([{"n": 1}], None, None))
        graphiti = MagicMock()
        graphiti.driver.clone = MagicMock(return_value=driver)

        with (
            patch.object(graph_module, "_get_graphiti", return_value=graphiti),
            patch.object(graph_module.settings, "graphiti_enabled", True),
        ):
            await graph_module.rename_episodes_to_document_keys(
                "org-1", {"doc:support:a": ["ep-1"]}
            )

        cypher = driver.execute_query.await_args.args[0]
        assert "SET e.name" in cypher
        for forbidden in ("DELETE", "CREATE", "MERGE", "Entity"):
            assert forbidden not in cypher
