from __future__ import annotations

import structlog

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.enrichment_policy import (
    GRAPHITI_EXTRACTION_VERSION,
    graph_episode_skip_reason,
)

logger = structlog.get_logger()


async def maybe_refresh_stale_graph(
    conn,
    *,
    artifact_id: str,
    extra: dict,
    org_id: str,
    kb_slug: str,
    path: str,
    content_type: str,
    belief_time_start: int,
    indexable_content: str,
) -> str | None:
    if not settings.graphiti_enabled:
        return None
    if extra.get("graphiti_extraction_version", 1) >= GRAPHITI_EXTRACTION_VERSION:
        return None

    graph_skip = graph_episode_skip_reason(indexable_content)
    if graph_skip:
        stale_ids = await pg_store.get_episode_ids_for_document_history(
            conn, org_id, [artifact_id]
        )
        if stale_ids:
            await graph_module.delete_kb_episodes(org_id, stale_ids)
        await pg_store.update_artifact_extra(
            conn,
            artifact_id,
            {
                "graphiti_episode_ids": [],
                "graphiti_episode_part_count": 0,
                "graphiti_episode_complete": True,
                "graphiti_episode_id": f"skipped:{graph_skip}",
                "graphiti_extraction_version": GRAPHITI_EXTRACTION_VERSION,
            },
        )
        logger.info(
            "graph_refresh_skipped",
            artifact_id=artifact_id,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            reason=graph_skip,
        )
        return f"skipped:{graph_skip}"

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    try:
        from procrastinate.exceptions import AlreadyEnqueued

        await proc_app.ingest_graphiti_episode.configure(  # type: ignore[attr-defined]
            queueing_lock=f"graphiti:{artifact_id}",
        ).defer_async(
            artifact_id=artifact_id,
            document_text=indexable_content,
            org_id=org_id,
            content_type=content_type,
            belief_time_start=belief_time_start,
            kb_slug=kb_slug,
            path=path,
            replace_stale=True,
        )
    except AlreadyEnqueued:
        logger.info(
            "graph_refresh_already_queued",
            artifact_id=artifact_id,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
        )
    logger.info(
        "graph_refresh_queued",
        artifact_id=artifact_id,
        org_id=org_id,
        kb_slug=kb_slug,
        path=path,
    )
    return "queued"
