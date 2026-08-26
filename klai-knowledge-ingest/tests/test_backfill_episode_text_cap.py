"""A graph backfill must not silently drop most of the corpus.

The original one-shot capped episode text at 4000 characters to "reduce LLM
calls". Measured on the Voys Dutch corpus on 2026-08-22: 488 of 726 documents
exceed that, and only 36% of 6.59M characters ever reached extraction. The
graph therefore had no facts about the second half of two documents in three,
and nothing recorded that it had happened.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import backfill


def test_cap_retains_the_bulk_of_a_real_corpus():
    """4000 retained 36% of the measured corpus; 30000 retains 91%."""
    assert backfill.MAX_TEXT_CHARS >= 30000, (
        "a cap this low drops most of every average document -- measured, "
        "488 of 726 Voys documents exceed 4000 characters"
    )


def test_cap_still_exists():
    """Not unbounded: one Voys artifact is 464k characters.

    Removing the limit entirely would let a single document exhaust the context
    window and a meaningful slice of the shared per-tenant rate budget.
    """
    assert backfill.MAX_TEXT_CHARS is not None
    assert backfill.MAX_TEXT_CHARS <= 60000


def test_incomplete_episode_list_is_retried_until_expected_part_count_lands():
    extra = {
        "graphiti_episode_ids": ["episode-1"],
        "graphiti_episode_part_count": 2,
        "graphiti_episode_complete": False,
    }

    assert backfill._has_graphiti_episode_record(extra) is False


@pytest.mark.asyncio
async def test_resume_prefers_episode_ids_list_without_reprocessing():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"org_id": "org-1"})
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "artifact-1",
                "kb_slug": "support",
                "path": "guide.md",
                "content_type": "text/markdown",
                "created_at": 1,
                "extra": json.dumps({"graphiti_episode_ids": ["ep-1", "ep-2"]}),
            }
        ]
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    qdrant = MagicMock()
    qdrant.scroll = AsyncMock(return_value=([], None))

    with (
        patch("knowledge_ingest.backfill.cross_org_admin_connection", return_value=ctx),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
    ):
        await backfill.main(org_id="org-1")

    qdrant.scroll.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_creates_and_records_every_episode_for_a_long_document():
    paragraph = ("A complete sentence. " * 1000).strip()
    document_text = f"{paragraph}\n\n{paragraph}"
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"org_id": "org-1"})
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "artifact-1",
                "kb_slug": "support",
                "path": "guide.md",
                "content_type": "text/markdown",
                "created_at": 1,
                "extra": None,
            }
        ]
    )
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    qdrant = MagicMock()
    qdrant.scroll = AsyncMock(
        return_value=(
            [SimpleNamespace(payload={"artifact_id": "artifact-1", "text": document_text})],
            None,
        )
    )
    ingest_episode = AsyncMock(side_effect=["episode-1", "episode-2"])

    with (
        patch("knowledge_ingest.backfill.cross_org_admin_connection", return_value=ctx),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch("knowledge_ingest.backfill._get_current_edge_count", return_value=0),
    ):
        await backfill.main(org_id="org-1")

    parts = [call.kwargs["document_text"] for call in ingest_episode.await_args_list]
    assert len(parts) == 2
    assert "\n\n".join(parts) == document_text
    assert all(len(part) <= backfill.MAX_TEXT_CHARS for part in parts)
    assert conn.execute.await_count == 4
    first_patch = json.loads(conn.execute.await_args_list[0].args[1])
    final_patch = json.loads(conn.execute.await_args_list[-1].args[1])
    assert first_patch == {
        "graphiti_episode_part_count": 2,
        "graphiti_episode_complete": False,
    }
    assert [call.args[2] for call in conn.execute.await_args_list[1:3]] == [
        "episode-1",
        "episode-2",
    ]
    assert final_patch["graphiti_episode_complete"] is True
