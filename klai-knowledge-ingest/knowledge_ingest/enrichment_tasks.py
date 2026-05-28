"""
Procrastinate task definitions for async chunk enrichment.

Two queues:
  enrich-interactive  -- single-doc uploads, drains first (higher priority)
  enrich-bulk         -- bulk LLM enrichment for crawled/imported pages

Crawl orchestration itself lives on the separate ``crawl-jobs`` queue
(``knowledge_ingest.crawl_tasks.run_crawl``) per
SPEC-INGEST-QUEUE-SEPARATION-001 — keeps I/O-bound crawls out of the
LLM-bound enrichment lane.

Both tasks call _enrich_document() which:
1. Loads a ContentTypeProfile for content-type-aware enrichment
2. Calls enrichment.enrich_chunks() with profile-specific question_focus and participant_context
3. Embeds enriched_text as vector_chunk (dense) for all chunks
4. Embeds questions as vector_questions (dense) when profile.hype_enabled(depth) is True
5. Embeds enriched_text as vector_sparse via BGE-M3 sidecar (falls back gracefully)
6. Upserts all vectors + payload to Qdrant (overwrites raw chunk points)

Procrastinate is imported lazily (inside init_app) so this module can be imported
in test environments where psycopg/libpq is not available.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from knowledge_ingest import (
    chunker,
    embedder,
    enrichment,
    kb_config,
    pg_store,
    qdrant_store,
    queues,
    sparse_embedder,
)
from knowledge_ingest.content_profiles import get_profile
from knowledge_ingest.db import cross_org_admin_connection, tenant_scoped_connection
from knowledge_ingest.document_normalizer import normalize_document_for_chunking
from knowledge_ingest.enrichment_policy import enrichment_skip_reason

logger = structlog.get_logger()

_procrastinate_app: Any = None


def get_app() -> Any:
    if _procrastinate_app is None:
        raise RuntimeError("Procrastinate app not initialised — call init_app() first")
    return _procrastinate_app


def init_app(connector: Any) -> Any:
    """
    Initialise Procrastinate App with the given async connector and register tasks.
    Called once from app.py lifespan after the DB pool is ready.
    procrastinate is imported here to avoid module-level psycopg dependency.
    """
    global _procrastinate_app
    import procrastinate

    _procrastinate_app = procrastinate.App(connector=connector)
    _register_tasks(_procrastinate_app)

    from knowledge_ingest.crawl_tasks import register_crawl_tasks

    register_crawl_tasks(_procrastinate_app)

    from knowledge_ingest.ingest_tasks import register_ingest_tasks

    register_ingest_tasks(_procrastinate_app)

    from knowledge_ingest.taxonomy_tasks import register_taxonomy_tasks

    register_taxonomy_tasks(_procrastinate_app)

    from knowledge_ingest.clustering_tasks import (
        register_auto_categorise_task,
        register_clustering_tasks,
    )

    register_clustering_tasks(_procrastinate_app)
    register_auto_categorise_task(_procrastinate_app)

    # SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-04: orchestrated connector-purge
    # task. Receives an enqueue from the portal DELETE endpoint and drives
    # the centralised ``connector_cleanup.purge_connector`` flow.
    from knowledge_ingest.connector_purge_tasks import (
        register_connector_purge_task,
    )

    register_connector_purge_task(_procrastinate_app)

    from knowledge_ingest.eval.ragas_runner import register_eval_tasks

    register_eval_tasks(_procrastinate_app)

    # SPEC-RAG-REBUILD-KB-001: operator-triggered KB rebuild backfill task.
    from knowledge_ingest.rebuild_tasks import register_rebuild_tasks

    register_rebuild_tasks(_procrastinate_app)

    # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-06: operator-triggered backfill
    # that scans an existing tenant's crawled_pages, detects login-walled
    # stubs, deletes the corresponding Qdrant points, and marks the page row
    # so the next crawl re-ingests through the new ingest-time guard.
    from knowledge_ingest.backfill_tasks import register_backfill_login_walls_task

    register_backfill_login_walls_task(_procrastinate_app)

    return _procrastinate_app


async def _load_and_enrich(artifact_id: str) -> None:
    """SPEC-INGEST-CONTENT-PG-001 (audit finding 1): single entry point for
    both enrichment task variants.

    Re-reads the artifact's content + extra_payload from PostgreSQL at
    execution time, re-derives the chunk + parent decomposition from the
    current document body, and delegates to the existing
    ``_enrich_document`` body. This is the canonical pattern that closes
    the race-window where a second direct-POST overwrote the raw Qdrant
    vectors while the worker still processed an older content snapshot
    frozen in the task args.

    If the artifact has been deleted between enqueue and dequeue (typically
    by the connector purge orchestrator), the function returns silently —
    same soft-skip semantics as ``ingest_graphiti_episode`` uses.
    """
    # SPEC-TI-003-FOLLOWUP-001: lookup-by-artifact_id is a cross-org read --
    # we don't know the org until we have the row. Use cross_org_admin so the
    # GUC marks the conn as a deliberate admin caller. Once we have org_id,
    # _enrich_document opens its own tenant_scoped_connection for the rest.
    async with cross_org_admin_connection() as admin_conn:
        artifact = await pg_store.read_artifact_for_enrichment(admin_conn, artifact_id)
    if artifact is None:
        logger.info("enrichment_aborted_artifact_missing", artifact_id=artifact_id)
        return

    extra: dict = artifact["extra"] or {}
    document_text: str = extra.get("document_text", "") or ""
    prechunked_skip = enrichment_skip_reason(
        chunk_count=int(extra.get("docling_chunk_count") or 0),
        extra_payload=extra,
    )
    if prechunked_skip is not None:
        logger.info(
            "enrichment_aborted_by_policy",
            artifact_id=artifact_id,
            kb_slug=artifact["kb_slug"],
            path=artifact["path"],
            reason=prechunked_skip,
            docling_chunk_count=extra.get("docling_chunk_count"),
        )
        await _set_direct_upload_index_status(artifact, "synced")
        return
    if not document_text:
        # Pre-SPEC-INGEST-CONTENT-PG-001 artifact rows may have no
        # document_text on extra. Without a body we cannot enrich; the
        # rebuild_kb path is the right tool to repair these legacy rows.
        logger.info(
            "enrichment_aborted_no_document_text",
            artifact_id=artifact_id,
            kb_slug=artifact["kb_slug"],
            path=artifact["path"],
        )
        await _set_direct_upload_index_status(artifact, "failed")
        return

    title: str = extra.get("title") or ""
    document_text = normalize_document_for_chunking(document_text)

    # Re-derive chunks deterministically from the current PG body so
    # Phase-2 always operates on the same content the artifact row claims
    # is current, regardless of what was in flight at enqueue time.
    children, parents = chunker.chunk_markdown_with_parents(document_text)
    chunks_text = [c.text for c in children]
    derived_skip = enrichment_skip_reason(chunk_count=len(chunks_text), extra_payload=extra)
    if derived_skip is not None:
        logger.info(
            "enrichment_aborted_by_policy",
            artifact_id=artifact_id,
            kb_slug=artifact["kb_slug"],
            path=artifact["path"],
            reason=derived_skip,
            chunks=len(chunks_text),
        )
        await _set_direct_upload_index_status(artifact, "synced")
        return
    parents_serialised: list[dict] = [
        {
            "text": p.text,
            "heading_path": p.heading_path,
            "token_count": chunker._approx_token_count(p.text),
            "position": p.position,
        }
        for p in parents
    ]
    parent_index_per_child: list[int | None] = [c.parent_index for c in children]
    heading_path_per_child: list[str] = [c.heading_path for c in children]

    await _enrich_document(
        org_id=artifact["org_id"],
        kb_slug=artifact["kb_slug"],
        path=artifact["path"],
        document_text=document_text,
        chunks=chunks_text,
        title=title,
        artifact_id=artifact_id,
        user_id=artifact["user_id"],
        extra_payload=extra,
        synthesis_depth=artifact["synthesis_depth"],
        content_type=artifact["content_type"] or "unknown",
        parents=parents_serialised,
        parent_index_per_child=parent_index_per_child,
        heading_path_per_child=heading_path_per_child,
    )


async def _set_direct_upload_index_status(artifact: dict, status: str) -> None:
    """Finish a direct-upload reindex status so the Sources UI cannot hang."""
    artifact_id = str(artifact.get("artifact_id") or "")
    org_id = str(artifact.get("org_id") or "")
    if not artifact_id or not org_id:
        return
    try:
        async with tenant_scoped_connection(org_id) as conn:
            await pg_store.set_artifact_index_status(conn, artifact_id, org_id, status)
    except Exception:
        logger.exception(
            "artifact_index_status_update_failed",
            artifact_id=artifact_id,
            org_id=org_id,
            status=status,
        )


def _register_tasks(procrastinate_app: Any) -> None:
    """Register task functions on the given App instance."""
    import procrastinate

    @procrastinate_app.task(
        queue=queues.ENRICH_INTERACTIVE, retry=procrastinate.RetryStrategy(max_attempts=2)
    )
    async def enrich_document_interactive(artifact_id: str) -> None:
        """Enrich chunks for a single-doc upload (high priority).

        SPEC-INGEST-CONTENT-PG-001: takes only ``artifact_id``; all other
        fields are loaded from PostgreSQL at execution time.
        """
        await _load_and_enrich(artifact_id)

    @procrastinate_app.task(
        queue=queues.ENRICH_BULK, retry=procrastinate.RetryStrategy(max_attempts=2)
    )
    async def enrich_document_bulk(artifact_id: str) -> None:
        """Enrich chunks for crawl/import jobs (lower priority).

        SPEC-INGEST-CONTENT-PG-001: takes only ``artifact_id``.
        """
        await _load_and_enrich(artifact_id)

    # Expose task functions via app attributes for use in ingest.py
    procrastinate_app.enrich_document_interactive = enrich_document_interactive  # type: ignore[attr-defined]
    procrastinate_app.enrich_document_bulk = enrich_document_bulk  # type: ignore[attr-defined]

    @procrastinate_app.task(
        queue=queues.GRAPHITI_BULK, retry=procrastinate.RetryStrategy(max_attempts=3)
    )
    async def ingest_graphiti_episode(
        artifact_id: str,
        document_text: str,
        org_id: str,
        content_type: str,
        belief_time_start: int,
    ) -> None:
        """Ingest a document into the Graphiti knowledge graph.

        Runs on the graphiti-bulk queue, which the worker drains AFTER enrich-bulk.
        This ensures enrichment LLM calls complete before Graphiti starts, preventing
        both from competing on the same 1 req/s upstream rate limit simultaneously.

        SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07: artifact-existence guard.
        If the artifact has been deleted between enqueue and dequeue (e.g. by
        the connector purge orchestrator) abort before writing to FalkorDB.
        Closes the regrow window — graphiti tasks have no
        ``source_connector_id`` arg, so the artifact-presence check is the
        canonical signal here.

        SPEC-TI-003-FOLLOWUP-001 AC-1: opens a tenant_scoped_connection on
        ``org_id`` so artifact_exists + update_artifact_extra both run with
        the RLS GUC pinned to this tenant.
        """
        from knowledge_ingest import pg_store

        async with tenant_scoped_connection(org_id) as conn:
            if not await pg_store.artifact_exists(conn, artifact_id):
                logger.info(
                    "graphiti_aborted_artifact_missing",
                    artifact_id=artifact_id,
                    org_id=org_id,
                )
                return
            logger.info(
                "graphiti_episode_started",
                artifact_id=artifact_id,
                org_id=org_id,
                content_type=content_type,
            )
            from knowledge_ingest import graph as graph_module

            episode_id = await graph_module.ingest_episode(
                artifact_id=artifact_id,
                document_text=document_text,
                org_id=org_id,
                content_type=content_type,
                belief_time_start=belief_time_start,
            )
            if episode_id:
                await pg_store.update_artifact_extra(
                    conn, artifact_id, {"graphiti_episode_id": episode_id}
                )

    procrastinate_app.ingest_graphiti_episode = ingest_graphiti_episode  # type: ignore[attr-defined]


async def _enrich_document(
    org_id: str,
    kb_slug: str,
    path: str,
    document_text: str,
    chunks: list[str],
    title: str,
    artifact_id: str,
    user_id: str | None,
    extra_payload: dict,
    synthesis_depth: int,
    content_type: str = "unknown",
    parents: list[dict] | None = None,
    parent_index_per_child: list[int | None] | None = None,
    heading_path_per_child: list[str] | None = None,
) -> None:
    """
    Core enrichment logic shared by both task variants.
    Uses content-type profiles for HyPE decisions and context strategy.
    Errors are logged but do not raise -- raw vectors remain in Qdrant.
    """
    # SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07: existence-guard.
    # If the source connector has been flipped to ``state='deleting'``
    # while this task was sitting in the queue, abort before doing any
    # write. Closes the in-flight regrow window — without this guard
    # any chunk we enrich here would be re-written to Qdrant after the
    # purge orchestrator's qdrant cleanup ran.
    source_connector_id = extra_payload.get("source_connector_id") if extra_payload else None
    if source_connector_id:
        from knowledge_ingest.connector_state import connector_is_active

        if not await connector_is_active(source_connector_id):
            logger.info(
                "enrichment_aborted_connector_inactive",
                connector_id=source_connector_id,
                artifact_id=artifact_id,
                kb_slug=kb_slug,
                path=path,
            )
            return
    t_total = time.monotonic()
    logger.info(
        "enrichment_started",
        kb_slug=kb_slug,
        path=path,
        org_id=org_id,
        artifact_id=artifact_id,
        chunks=len(chunks),
        depth=synthesis_depth,
        type=content_type,
    )
    try:
        profile = get_profile(content_type)

        # Build participant context string if available
        participants = extra_payload.get("participants") if extra_payload else None
        participant_context_str = ""
        if participants:
            names = ", ".join(
                f"{p.get('name', '?')} ({p.get('role', '')})" for p in participants if p.get("name")
            )
            if names:
                participant_context_str = (
                    f"\nDeelnemers: {names}. "
                    "Gebruik de deelnemerslijst om voornaamwoorden op te lossen waar mogelijk.\n"
                )

        # Step 1: LLM enrichment (context prefix + HyPE questions per chunk)
        # Extract source-aware enrichment fields from extra_payload (SPEC-KB-021)
        kb_name_val = (extra_payload or {}).get("kb_name", "")
        connector_type_val = (extra_payload or {}).get("connector_type", "")
        source_domain_val = (extra_payload or {}).get("source_domain", "")

        # SPEC-RAG-CONTEXTUAL-001: generate one document summary per artifact
        # (Anthropic-pattern). Cached in extra_payload so a re-ingest of the
        # same artifact reuses it. The chunk-enrichment prompt then feeds
        # the summary instead of the full document body — ~8x reduction in
        # per-chunk input tokens for a 20-chunk document.
        document_summary_val = (extra_payload or {}).get("document_summary", "")
        document_language_val = (extra_payload or {}).get("document_language") or None
        if not document_summary_val and document_text:
            from knowledge_ingest import contextual

            if document_language_val is None:
                document_language_val = contextual.detect_language(document_text)
            document_summary_val = await contextual.generate_document_summary(
                text=document_text,
                title=title,
                language=document_language_val,
            )
            # Persist on extra_payload so callers (qdrant_store) can store it
            # on the artifact row for cache hits on re-ingest.
            if extra_payload is None:
                extra_payload = {}
            extra_payload["document_summary"] = document_summary_val
            extra_payload["document_language"] = document_language_val

        t0 = time.monotonic()
        enriched_chunks = await enrichment.enrich_chunks(
            document_text=document_text,
            chunks=chunks,
            title=title,
            path=path,
            question_focus=profile.hype_question_focus,
            participant_context=participant_context_str,
            context_strategy=profile.context_strategy,
            context_tokens=profile.context_tokens_max,
            kb_name=kb_name_val,
            connector_type=connector_type_val,
            source_domain=source_domain_val,
            artifact_id=artifact_id,
            document_summary=document_summary_val,
            document_language=document_language_val,
            heading_paths=heading_path_per_child,
        )
        llm_ms = int((time.monotonic() - t0) * 1000)

        # Anchor text augmentation: append vocabulary from pages linking to this page.
        # Modifies enriched_text only -- original_text and context_prefix stay unchanged.
        # SPEC-CRAWLER-003 R9, R10, R11
        anchor_texts_raw = extra_payload.get("anchor_texts", []) if extra_payload else []
        if anchor_texts_raw:
            unique_anchors = list(dict.fromkeys(anchor_texts_raw))
            anchor_block = "\n\nAlso known as: " + " | ".join(unique_anchors)
            for ec in enriched_chunks:
                ec.enriched_text += anchor_block

        # Step 2: Embed dense (TEI) + sparse (BGE-M3 GPU sidecar) in parallel.
        # Wrapped individually so we get separate tei_ms / sparse_ms despite parallel execution.
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]

        async def _timed_dense() -> tuple[list, int]:
            t = time.monotonic()
            vecs = await embedder.embed(enriched_texts)
            return vecs, int((time.monotonic() - t) * 1000)

        async def _timed_sparse() -> tuple[list, int]:
            t = time.monotonic()
            vecs = await sparse_embedder.embed_sparse_batch(enriched_texts)
            return vecs, int((time.monotonic() - t) * 1000)

        (chunk_vectors, tei_ms), (sparse_vectors, sparse_ms) = await asyncio.gather(
            _timed_dense(), _timed_sparse()
        )

        # Step 3: Embed questions based on profile (vector_questions)
        question_vectors: list[list[float] | None]
        if profile.hype_enabled(synthesis_depth):
            question_strings = [
                " ".join(ec.questions) if ec.questions else ec.original_text
                for ec in enriched_chunks
            ]
            raw_q_vectors = await embedder.embed(question_strings)
            question_vectors = list(raw_q_vectors)
        else:
            question_vectors = [None] * len(enriched_chunks)

        # Refresh visibility from kb_config at write time — catches any visibility
        # change that happened while this task was queued or running.
        # SPEC-TI-003-FOLLOWUP-001 AC-1: tenant_scoped_connection so kb_config +
        # insert_parent_chunks see the RLS GUC for this org.
        async with tenant_scoped_connection(org_id) as conn:
            extra_payload["visibility"] = await kb_config.get_kb_visibility(conn, org_id, kb_slug)

            # SPEC-RAG-PARENT-CHILD-001: persist parents to Postgres NOW so the
            # generated row ids can be threaded into each child's Qdrant payload.
            # Skipped (parent_chunk_ids list of None) when this ingest path was
            # called without parents — keeps backward-compat for any caller that
            # hasn't been switched to chunk_markdown_with_parents yet.
            parent_chunk_ids: list[int | None] = [None] * len(enriched_chunks)
            if parents and parent_index_per_child:
                from knowledge_ingest import pg_store

                await pg_store.delete_parent_chunks_for_artifact(conn, artifact_id)
                inserted_ids = await pg_store.insert_parent_chunks(
                    conn,
                    artifact_id=artifact_id,
                    org_id=org_id,
                    parents=parents,
                )
                for i, parent_idx in enumerate(parent_index_per_child):
                    if parent_idx is not None and 0 <= parent_idx < len(inserted_ids):
                        parent_chunk_ids[i] = inserted_ids[parent_idx]

        # Step 4: Upsert enriched chunks to Qdrant
        t0 = time.monotonic()
        await qdrant_store.upsert_enriched_chunks(
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            enriched_chunks=enriched_chunks,
            chunk_vectors=chunk_vectors,
            question_vectors=question_vectors,
            sparse_vectors=sparse_vectors,
            artifact_id=artifact_id,
            extra_payload=extra_payload,
            user_id=user_id,
            content_type=content_type,
            belief_time_start=extra_payload.get("belief_time_start") if extra_payload else None,
            belief_time_end=extra_payload.get("belief_time_end") if extra_payload else None,
            parent_chunk_ids=parent_chunk_ids,
        )
        qdrant_ms = int((time.monotonic() - t0) * 1000)

        await _set_direct_upload_index_status(
            {"artifact_id": artifact_id, "org_id": org_id},
            "synced",
        )

        total_ms = int((time.monotonic() - t_total) * 1000)
        enriched_count = sum(1 for ec in enriched_chunks if ec.context_prefix)
        sparse_success_count = sum(1 for sv in sparse_vectors if sv is not None)
        logger.info(
            "enrichment_complete",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            artifact_id=artifact_id,
            chunks=len(chunks),
            enriched=enriched_count,
            depth=synthesis_depth,
            type=content_type,
            sparse_ok=sparse_success_count,
            llm_ms=llm_ms,
            tei_ms=tei_ms,
            sparse_ms=sparse_ms,
            qdrant_ms=qdrant_ms,
            total_ms=total_ms,
        )

    except enrichment.EnrichmentError:
        # Fail-loudly (SPEC-KB-021): LLM enrichment failure must propagate so
        # Procrastinate retries the job.  Raw chunks from Phase 1 stay in Qdrant
        # as a temporary fallback; they will be overwritten on successful retry.
        # After max_attempts the job lands in permanent-failed state — visible in
        # logs and the Procrastinate dashboard.
        total_ms = int((time.monotonic() - t_total) * 1000)
        logger.exception(
            "enrichment_failed_will_retry",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            artifact_id=artifact_id,
            total_ms=total_ms,
        )
        await _set_direct_upload_index_status(
            {"artifact_id": artifact_id, "org_id": org_id},
            "failed",
        )
        raise  # Procrastinate retry handles this

    except Exception:
        # Non-enrichment failures (embedding, Qdrant, network): log and swallow.
        # Raw vectors remain in Qdrant — document is still searchable with
        # basic embeddings.  These are infrastructure issues, not data quality
        # issues, so retrying the enrichment LLM call would not help.
        total_ms = int((time.monotonic() - t_total) * 1000)
        logger.exception(
            "enrichment_infra_failed",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            artifact_id=artifact_id,
            total_ms=total_ms,
        )
        await _set_direct_upload_index_status(
            {"artifact_id": artifact_id, "org_id": org_id},
            "failed",
        )
