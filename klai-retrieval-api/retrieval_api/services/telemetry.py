"""SPEC-PRIVACY-QUERY-SHADOW-001 — fire-and-forget writer for telemetry.query_shadow.

Inserts the embedding + symbolic features for a /retrieve call into the
``telemetry.query_shadow`` table created by the post-deploy SQL of
migration g5h6i7j8k9l0. Writes are non-blocking — the response path
schedules `write_shadow` via `asyncio.create_task` and proceeds without
awaiting; failures are logged + counted but never surface to the user.

The writer reuses the existing portal-events asyncpg pool (`get_pool()`)
because retrieval-api already connects to the same Postgres database as
the `klai` superuser for `product_events` writes (REQ-7).
"""

from __future__ import annotations

import asyncio
import json

import structlog

from retrieval_api.metrics import telemetry_shadow_drop_total
from retrieval_api.services.events import get_pool

logger = structlog.get_logger()

_INSERT_SQL = """
    INSERT INTO telemetry.query_shadow
        (request_id, org_id, embedding, features, band, chunk_ids, reranker_top1)
    VALUES ($1, $2, $3::vector, $4::jsonb, $5, $6, $7)
    ON CONFLICT (request_id) DO NOTHING
"""


def _format_vector(values: list[float] | None) -> str | None:
    """Encode a Python list as the pgvector literal '[v1,v2,...]'.

    pgvector accepts either binary or text input; we use text so the
    asyncpg driver doesn't need a custom codec. Returns None for
    a missing embedding (still allowed by the schema's nullable column).
    """
    if values is None:
        return None
    # Use repr-friendly fast path; pgvector parses scientific notation.
    return "[" + ",".join(format(v, ".7g") for v in values) + "]"


async def _do_insert(
    *,
    request_id: str,
    org_id: str,
    embedding: list[float] | None,
    features: dict,
    band: str | None,
    chunk_ids: list[str],
    reranker_top1: float | None,
) -> None:
    pool = get_pool()
    if pool is None:
        # pool not yet initialized — happens in tests + early-startup.
        # Counted as drop so the metric stays honest about coverage.
        telemetry_shadow_drop_total.labels(reason="no_pool").inc()
        return
    try:
        await pool.execute(
            _INSERT_SQL,
            request_id,
            org_id,
            _format_vector(embedding),
            json.dumps(features),
            band,
            chunk_ids,
            reranker_top1,
        )
    except Exception:
        telemetry_shadow_drop_total.labels(reason="db_error").inc()
        logger.warning(
            "telemetry_shadow_insert_failed",
            request_id=request_id,
            org_id=org_id,
            exc_info=True,
        )


def write_shadow(
    *,
    request_id: str,
    org_id: str,
    embedding: list[float] | None,
    features: dict,
    band: str | None,
    chunk_ids: list[str],
    reranker_top1: float | None,
) -> None:
    """Schedule a non-blocking shadow-store INSERT.

    Returns immediately. The actual asyncpg execute runs in a background
    asyncio task; failures bump the drop counter and emit a warning but
    never propagate to the response path (REQ-7 fail-and-forget contract).
    """
    try:
        asyncio.create_task(  # noqa: RUF006 — drops are tracked via metric instead
            _do_insert(
                request_id=request_id,
                org_id=org_id,
                embedding=embedding,
                features=features,
                band=band,
                chunk_ids=chunk_ids,
                reranker_top1=reranker_top1,
            )
        )
    except RuntimeError:
        # No running loop — should not happen in production (every code
        # path lives inside a request handler) but defensive for tests.
        telemetry_shadow_drop_total.labels(reason="no_loop").inc()
        logger.warning("telemetry_shadow_no_running_loop", request_id=request_id)
