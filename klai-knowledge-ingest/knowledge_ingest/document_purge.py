"""Delete a document for good: every version, from every store.

Replacing a document keeps its history (the old row is closed and linked to
its successor through ``superseded_by``). Deleting it must not: the owner
expects a removed document to be gone, including the full text that earlier
versions still carry in ``extra->>'document_text'``, their parent chunks,
image rows, vector points and graph episodes.

Order, shared by every delete path:

1. Close the active rows. Search stops returning them and the enrichment and
   graph workers abort on a non-active artifact, so no worker recreates
   external state while the delete runs.
2. Walk ``superseded_by`` back to every earlier version.
3. Delete the graph episodes and the vector points of all versions.
4. Hard-delete the rows last. PostgreSQL stays the retry ledger: when an
   external delete raises, the rows remain (closed) and a retried delete
   finds the same artifact and episode ids again.
"""

from __future__ import annotations

import asyncpg

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store, qdrant_store


async def purge_documents(
    conn: asyncpg.Connection, org_id: str, artifact_ids: list[str]
) -> tuple[int, int]:
    """Delete the documents headed by ``artifact_ids`` and all their versions.

    ``conn`` must be scoped to ``org_id``; every statement is bound to it as
    well. Returns ``(artifact rows deleted, graph episodes deleted)``.
    """
    if not artifact_ids:
        return 0, 0
    await pg_store.close_artifacts(conn, org_id, artifact_ids)
    history_ids, paths = await pg_store.list_document_history(conn, org_id, artifact_ids)
    if not history_ids:
        return 0, 0
    episode_ids = await pg_store.get_episode_ids_for_document_history(conn, org_id, history_ids)
    await graph_module.delete_kb_episodes(org_id, episode_ids)
    await qdrant_store.delete_artifacts(org_id, history_ids, paths)
    deleted = await pg_store.delete_artifacts(conn, org_id, history_ids, paths)
    return deleted, len(episode_ids)
