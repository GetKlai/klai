"""
Procrastinate task definitions for async chunk enrichment.

Two queues:
  enrich-interactive  -- single-doc uploads, drains first (higher priority)
  enrich-bulk         -- crawl/import jobs

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

from knowledge_ingest import embedder, enrichment, kb_config, qdrant_store, sparse_embedder
from knowledge_ingest.content_profiles import get_profile
from knowledge_ingest.db import get_pool

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
    import procrastinate  # noqa: PLC0415 — intentional lazy import

    _procrastinate_app = procrastinate.App(connector=connector)
    _register_tasks(_procrastinate_app)

    from knowledge_ingest.crawl_tasks import register_crawl_tasks  # noqa: PLC0415
    register_crawl_tasks(_procrastinate_app)

    from knowledge_ingest.ingest_tasks import register_ingest_tasks  # noqa: PLC0415
    register_ingest_tasks(_procrastinate_app)

    return _procrastinate_app


def _register_tasks(procrastinate_app: Any) -> None:
    """Register task functions on the given App instance."""
    import procrastinate  # noqa: PLC0415 — intentional lazy import

    @procrastinate_app.task(queue="enrich-interactive", retry=procrastinate.RetryStrategy(max_attempts=2))
    async def enrich_document_interactive(
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
    ) -> None:
        """Enrich chunks for a single-doc upload (high priority)."""
        await _enrich_document(
            org_id, kb_slug, path, document_text, chunks, title,
            artifact_id, user_id, extra_payload, synthesis_depth,
            content_type=content_type,
        )

    @procrastinate_app.task(queue="enrich-bulk", retry=procrastinate.RetryStrategy(max_attempts=2))
    async def enrich_document_bulk(
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
    ) -> None:
        """Enrich chunks for crawl/import jobs (lower priority)."""
        await _enrich_document(
            org_id, kb_slug, path, document_text, chunks, title,
            artifact_id, user_id, extra_payload, synthesis_depth,
            content_type=content_type,
        )

    # Expose task functions via app attributes for use in ingest.py
    procrastinate_app.enrich_document_interactive = enrich_document_interactive  # type: ignore[attr-defined]
    procrastinate_app.enrich_document_bulk = enrich_document_bulk  # type: ignore[attr-defined]


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
) -> None:
    """
    Core enrichment logic shared by both task variants.
    Uses content-type profiles for HyPE decisions and context strategy.
    Errors are logged but do not raise -- raw vectors remain in Qdrant.
    """
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
                f"{p.get('name', '?')} ({p.get('role', '')})"
                for p in participants
                if p.get("name")
            )
            if names:
                participant_context_str = (
                    f"\nDeelnemers: {names}. "
                    "Gebruik de deelnemerslijst om voornaamwoorden op te lossen waar mogelijk.\n"
                )

        # Step 1: LLM enrichment (context prefix + HyPE questions per chunk)
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
        )
        llm_ms = int((time.monotonic() - t0) * 1000)

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
        pool = await get_pool()
        extra_payload["visibility"] = await kb_config.get_kb_visibility(org_id, kb_slug, pool)

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
        )
        qdrant_ms = int((time.monotonic() - t0) * 1000)

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

    except Exception as exc:
        total_ms = int((time.monotonic() - t_total) * 1000)
        logger.error(
            "enrichment_failed",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            artifact_id=artifact_id,
            total_ms=total_ms,
            exc_info=True,
        )
        # Raw vectors remain in Qdrant -- document is still searchable
