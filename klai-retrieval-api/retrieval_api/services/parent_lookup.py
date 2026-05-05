"""SPEC-RAG-PARENT-CHILD-001 — fetch parent text for child chunks.

Retrieval matches on small "child" chunks (Qdrant). The response then
swaps the child text for the corresponding parent's broader-context
text so the LLM sees more narrative.

Children carry ``parent_chunk_id`` in their Qdrant payload; this
module batch-fetches the parent rows from ``knowledge.parent_chunks``
in a single query.

Fail-open per REQ-3: when no DB pool is available, when a parent_id
is missing in the table, or when the query fails, the caller falls
through to using the child's own text.
"""

from __future__ import annotations

from typing import Iterable

import structlog

from retrieval_api.services import events

logger = structlog.get_logger()


async def fetch_parents(parent_ids: Iterable[int]) -> dict[int, str]:
    """Return ``{parent_chunk_id: text}`` for the given ids.

    Missing or unfetchable ids are simply absent from the result —
    callers MUST check membership and fall back to child text.
    """
    unique_ids = sorted({int(pid) for pid in parent_ids if pid is not None})
    if not unique_ids:
        return {}

    pool = events.get_pool()
    if pool is None:
        logger.warning("parent_lookup_no_pool count=%d", len(unique_ids))
        return {}

    try:
        rows = await pool.fetch(
            "SELECT id, text FROM knowledge.parent_chunks WHERE id = ANY($1::bigint[])",
            unique_ids,
        )
    except Exception as exc:
        logger.warning(
            "parent_lookup_failed count=%d error=%s",
            len(unique_ids),
            str(exc)[:200],
        )
        return {}

    return {int(row["id"]): row["text"] for row in rows}
