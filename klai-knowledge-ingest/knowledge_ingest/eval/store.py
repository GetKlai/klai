"""
asyncpg helpers for rag_eval_results storage.

Reuses the shared pool from knowledge_ingest.db — no separate pool is created.
"""

import json

from knowledge_ingest.db import get_pool


async def insert_eval_row(
    suite: str,
    query_id: str,
    context_precision: float | None,
    context_recall: float | None,
    faithfulness: float | None,
    answer_relevance: float | None,
    retrieved_chunk_ids: list[str],
    retrieval_ms: int | None,
    total_tokens: int | None,
    meta: dict,
    variant: str = "baseline",
) -> int:
    """Insert one evaluation row and return its generated id.

    All four metric columns are nullable so failure-mode rows (REQ-3) can be
    stored with NULL metrics alongside a meta.error description.
    """
    pool = await get_pool()
    row_id = await pool.fetchval(
        """
        INSERT INTO knowledge.rag_eval_results
          (suite, variant, query_id,
           context_precision, context_recall, faithfulness, answer_relevance,
           retrieved_chunk_ids, retrieval_ms, total_tokens, meta)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
        """,
        suite,
        variant,
        query_id,
        context_precision,
        context_recall,
        faithfulness,
        answer_relevance,
        retrieved_chunk_ids,
        retrieval_ms,
        total_tokens,
        json.dumps(meta),
    )
    return int(row_id)


async def get_eval_index_names() -> set[str]:
    """Query pg_indexes and return the index names on rag_eval_results.

    Used in tests to assert that the migration applied both required indexes.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'rag_eval_results'
          AND schemaname = 'knowledge'
        """,
    )
    return {row["indexname"] for row in rows}
