"""Version-gated knowledge-graph refresh for already-ingested documents.

A refresh (connector re-sync with unchanged content, or an upload reindex)
rebuilds the graph episodes of a document whose extraction predates
``GRAPHITI_EXTRACTION_VERSION`` — without re-chunking, re-embedding, or
re-enrichment. The caller MUST pass a connection scoped to exactly this
``org_id`` (asyncpg tenant GUCs are connection-local).
"""

from __future__ import annotations

import time

import asyncpg
import structlog

from knowledge_ingest import derived_state, pg_store
from knowledge_ingest import graph as graph_module
from knowledge_ingest.config import settings
from knowledge_ingest.derived_state import FAILED, NOT_APPLICABLE, PENDING, ProductState
from knowledge_ingest.enrichment_policy import (
    GRAPHITI_EXTRACTION_VERSION,
    graph_episode_skip_reason,
)

logger = structlog.get_logger()

# A version-only refresh re-extracts a document whose content did not change,
# so it is pure LLM spend with no deadline, and after a version bump every
# active document of an org qualifies on its next unchanged re-sync. 300 a
# day drains a backlog of a few thousand documents over about ten daily syncs
# instead of in one burst. At the measured ~28 LLM calls per episode part that
# is ~8.4k calls a day, about a tenth of a 1 req/s upstream budget, which
# leaves the rest for live ingest.
GRAPH_REFRESH_DAILY_CAP = 300

# How long an unchanged re-sync leaves a document alone after its graph job
# used up every retry. The job's own retries already span ~24h, so whatever
# failed it (LLM budget, provider or FalkorDB outage) outlasted a day; a week
# gives such an outage time to be fixed while each failing document costs at
# most one retry cycle per week. After that it is eligible again under the
# daily cap and resumes after the parts it stored, so nothing stays partial
# for good.
EXHAUSTED_COOL_OFF_SECONDS = 7 * 86_400


async def maybe_refresh_stale_graph(
    conn: asyncpg.Connection,
    *,
    artifact_id: str,
    extra: dict,
    org_id: str,
    kb_slug: str,
    path: str,
    content_type: str,
    belief_time_start: int,
    indexable_content: str,
    user_requested: bool = False,
) -> str | None:
    """Queue (or apply) a graph rebuild when the artifact's extraction is stale.

    ``user_requested`` is an explicit reindex of this one document: it skips
    the cool-off and the daily cap, which exist to bound automatic refreshes.

    Returns ``"queued"``, ``"already_queued"``, ``"skipped:<reason>"``,
    ``"deferred:<reason>"``, ``"held:<reason>"`` (failed, and re-queuing
    cannot succeed), or ``None`` when the graph is current or graphiti is
    disabled. Never raises:
    the refresh is opportunistic — the caller's contract (content-unchanged
    skip, upload reindex) must not fail on a FalkorDB or queue hiccup, matching
    how the ingest route swallows its other graph operations ("a stranded
    episode costs a citation, not correctness").
    """
    if not settings.graphiti_enabled:
        return None
    try:
        return await _refresh_stale_graph(
            conn,
            artifact_id=artifact_id,
            extra=extra,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            content_type=content_type,
            belief_time_start=belief_time_start,
            indexable_content=indexable_content,
            user_requested=user_requested,
        )
    except Exception:
        logger.exception(
            "graph_refresh_failed",
            artifact_id=artifact_id,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
        )
        return None


def _cannot_succeed(state: ProductState, extra: dict) -> bool:
    from knowledge_ingest.falkordb_probe import graph_writer_build

    if state == ProductState(FAILED, "no_stored_text"):
        return True
    return state == ProductState(FAILED, "quarantined") and (
        extra.get("graphiti_quarantine_build") == graph_writer_build()
    )


async def _refresh_stale_graph(
    conn: asyncpg.Connection,
    *,
    artifact_id: str,
    extra: dict,
    org_id: str,
    kb_slug: str,
    path: str,
    content_type: str,
    belief_time_start: int,
    indexable_content: str,
    user_requested: bool,
) -> str | None:
    if extra.get("graphiti_extraction_version", 1) >= GRAPHITI_EXTRACTION_VERSION:
        return None

    graph_skip = graph_episode_skip_reason(indexable_content)
    if graph_skip:
        await graph_module.retire_earlier_extractions(conn, org_id, artifact_id, keep=[])
        await pg_store.update_artifact_extra(
            conn,
            artifact_id,
            {
                "graphiti_episode_ids": [],
                "graphiti_episode_part_count": 0,
                "graphiti_episode_complete": True,
                "graphiti_episode_id": None,
                "graphiti_extraction_version": GRAPHITI_EXTRACTION_VERSION,
            },
        )
        # The newer extraction rules re-decide this version: reopen, then record.
        await derived_state.set_product_state(conn, artifact_id, "graph", PENDING)
        await derived_state.set_product_state(
            conn, artifact_id, "graph", NOT_APPLICABLE, graph_skip
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

    state = derived_state.product_state(extra, "graph")
    if not user_requested and _cannot_succeed(state, extra):
        # The document already says failed for this reason, and a new job
        # would fail the same way and alert again. Only new content (a new
        # version), a new writer build releasing a quarantine, or a reindex
        # by a person queues it again.
        logger.info(
            "graph_refresh_held",
            artifact_id=artifact_id,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            reason=state.reason,
        )
        return f"held:{state.reason}"

    if not user_requested:
        deferred = None
        failed_at = extra.get("graph_state_at") or extra.get("graphiti_exhausted_at")
        if (
            state == ProductState(FAILED, "retries_exhausted")
            and failed_at
            and time.time() - failed_at < EXHAUSTED_COOL_OFF_SECONDS
        ):
            deferred = "cool_off"
        elif not await pg_store.reserve_graph_refresh_slot(conn, org_id, GRAPH_REFRESH_DAILY_CAP):
            deferred = "daily_cap"
        if deferred:
            logger.info(
                "graph_refresh_deferred",
                artifact_id=artifact_id,
                org_id=org_id,
                kb_slug=kb_slug,
                path=path,
                reason=deferred,
            )
            return f"deferred:{deferred}"

    from procrastinate.exceptions import AlreadyEnqueued

    from knowledge_ingest import enrichment_tasks

    proc_app = enrichment_tasks.get_app()
    # Pending before the defer, so a job that runs at once cannot have its
    # outcome overwritten. When no job could be queued the keys this function
    # just changed are put back as they were, because the previous extraction
    # still stands: the one write of the state outside set_product_state.
    await derived_state.set_product_state(conn, artifact_id, "graph", PENDING)
    try:
        # ``queueing_lock`` dedups only while the earlier job is still todo;
        # ``lock`` also serialises against a job that is already executing,
        # so a refresh can never delete the episodes an in-flight extraction
        # for the same artifact is appending.
        await proc_app.ingest_graphiti_episode.configure(  # type: ignore[attr-defined]
            lock=f"graphiti:{artifact_id}",
            queueing_lock=f"graphiti:{artifact_id}",
        ).defer_async(
            artifact_id=artifact_id,
            org_id=org_id,
            content_type=content_type,
            belief_time_start=belief_time_start,
            kb_slug=kb_slug,
            path=path,
            replace_stale=True,
        )
    except Exception as exc:
        # No job was created, so the slot goes back whatever the reason.
        if not user_requested:
            await pg_store.release_graph_refresh_slot(conn, org_id)
        if not isinstance(exc, AlreadyEnqueued):
            await pg_store.update_artifact_extra(
                conn,
                artifact_id,
                {key: extra.get(key) for key in ("graph_state", "graph_reason", "graph_state_at")},
            )
            raise
        logger.info(
            "graph_refresh_already_queued",
            artifact_id=artifact_id,
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
        )
        return "already_queued"
    logger.info(
        "graph_refresh_queued",
        artifact_id=artifact_id,
        org_id=org_id,
        kb_slug=kb_slug,
        path=path,
    )
    return "queued"
