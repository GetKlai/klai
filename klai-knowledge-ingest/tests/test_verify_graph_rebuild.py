"""The completeness check must look in both directions.

GetKlai/klai#1148. Checking only that every episode resolves to a document
misses everything MISSING; checking only that every document has an episode
misses everything LEFT OVER. The rebuild cleaned episodes by walking
artifacts.extra->>'graphiti_episode_id', and 109 episodes whose artifact link
had been lost were never on the list -- they survived and kept serving stale
facts, invisible to any check driven from Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import verify_graph_rebuild as verify_mod

ORG = "org-1"
KBS = ["support"]


def _conn(pending: int, live: list[tuple[str, str]]) -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=pending)
    conn.fetch = AsyncMock(return_value=[{"kb_slug": kb, "path": p} for kb, p in live])
    return conn


def _admin(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def _run(episodes, orphans, pending, live):
    conn = _conn(pending, live)
    with (
        patch.object(verify_mod, "_graph_episodes", AsyncMock(return_value=episodes)),
        patch.object(verify_mod, "_orphan_edge_count", AsyncMock(return_value=orphans)),
        patch.object(verify_mod, "cross_org_admin_connection", return_value=_admin(conn)),
    ):
        return await verify_mod.verify(ORG, "2026-08-24", KBS)


@pytest.mark.asyncio
async def test_a_clean_rebuild_reports_every_count_zero():
    counts = await _run(
        episodes=[("doc:support:a", "2026-08-24")],
        orphans=0,
        pending=0,
        live=[("support", "a")],
    )
    assert not any(counts.values()), counts


@pytest.mark.asyncio
async def test_a_stale_episode_is_caught_from_the_graph_side():
    """The 109 survivors had no artifact link, so only the graph knows them."""
    counts = await _run(
        episodes=[("doc:support:a", "2026-08-24"), ("doc:support:old", "2026-05-01")],
        orphans=0,
        pending=0,
        live=[("support", "a")],
    )
    assert counts["stale_episodes"] == 1
    # And it is NOT reported as a dangling episode -- that would hide it among
    # a different class of problem.
    assert counts["episodes_without_document"] == 0


@pytest.mark.asyncio
async def test_a_document_without_a_fresh_episode_is_caught():
    counts = await _run(
        episodes=[("doc:support:a", "2026-08-24")],
        orphans=0,
        pending=0,
        live=[("support", "a"), ("support", "b")],
    )
    assert counts["documents_without_episode"] == 1


@pytest.mark.asyncio
async def test_an_episode_pointing_at_a_deleted_document_is_caught():
    counts = await _run(
        episodes=[("doc:support:gone", "2026-08-24")],
        orphans=0,
        pending=0,
        live=[],
    )
    assert counts["episodes_without_document"] == 1


@pytest.mark.asyncio
async def test_episodes_from_other_knowledge_bases_are_out_of_scope():
    """A rebuild scoped to Dutch KBs must not report the English one as stale."""
    counts = await _run(
        episodes=[("doc:support:a", "2026-08-24"), ("doc:ascend:x", "2026-03-01")],
        orphans=0,
        pending=0,
        live=[("support", "a")],
    )
    assert not any(counts.values()), counts
