"""Real-Postgres check of which graph episodes count as a document's current extraction.

Per document the graph holds the facts of exactly one version: the newest one
that was actually extracted. ``get_alive_episode_uuids_for_org`` is that rule in
SQL, and the org-wide orphan sweep and ``scripts/prune_stale_graph.py`` delete
everything it does not return.
"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from knowledge_ingest import pg_store

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")
_ACTIVE = 253402300800

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


async def _insert(
    conn: asyncpg.Connection,
    org_id: str,
    path: str,
    extra: dict,
    *,
    superseded_by: str | None = None,
    active: bool = False,
) -> str:
    artifact_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO knowledge.artifacts (
            id, org_id, provenance_type, assertion_mode, belief_time_start,
            belief_time_end, superseded_by, created_at, kb_slug, path, extra
        ) VALUES ($1, $2, 'observed', 'factual', 1, $3, $4, 1, 'docs', $5, $6::jsonb)
        """,
        artifact_id,
        org_id,
        _ACTIVE if active else 2,
        superseded_by,
        path,
        json.dumps(extra),
    )
    return artifact_id


async def _chain(conn: asyncpg.Connection, org_id: str, path: str, extras: list[dict]) -> None:
    """Insert one document's versions, newest last in ``extras``; the newest is active."""
    newer: str | None = None
    for position, extra in enumerate(reversed(extras)):
        newer = await _insert(conn, org_id, path, extra, superseded_by=newer, active=position == 0)


@pytest.fixture
async def tenant():
    assert _DSN is not None
    org_id = f"org-alive-{uuid.uuid4().hex}"
    conn = await asyncpg.connect(_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
    try:
        yield conn, org_id
    finally:
        await conn.execute("DELETE FROM knowledge.artifacts WHERE org_id = $1", org_id)
        await conn.close()


async def test_only_the_newest_extracted_version_of_each_document_is_alive(tenant) -> None:
    conn, org_id = tenant
    # Re-extracted: the replaced version's episodes are history, not search content.
    await _chain(
        conn,
        org_id,
        "re-extracted",
        [{"graphiti_episode_ids": ["old-1", "old-2"]}, {"graphiti_episode_ids": ["new-1"]}],
    )
    # Newest version held out of the graph: the previous extraction stands in.
    await _chain(
        conn,
        org_id,
        "skipped",
        [
            {"graphiti_episode_ids": ["stand-in-older"]},
            {"graphiti_episode_ids": ["stand-in"]},
            {"graphiti_episode_id": "skipped:navigation"},
        ],
    )
    # Newest version still queued (no episode keys yet), two versions without
    # an extraction in a row: the walk goes back to the nearest extracted one.
    await _chain(
        conn,
        org_id,
        "queued",
        [
            {"graphiti_episode_id": "legacy-scalar"},
            {"graphiti_episode_ids": []},
            {},
        ],
    )
    # Removed document: no active row, so nothing of it is alive.
    await _insert(conn, org_id, "removed", {"graphiti_episode_ids": ["removed-1"]})

    alive = await pg_store.get_alive_episode_uuids_for_org(conn, org_id)

    assert alive == {"new-1", "stand-in", "legacy-scalar"}
