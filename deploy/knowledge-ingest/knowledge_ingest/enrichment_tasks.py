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

import logging
from typing import Any

from knowledge_ingest import embedder, enrichment, qdrant_store, sparse_embedder
from knowledge_ingest.content_profiles import get_profile

logger = logging.getLogger(__name__)

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

        enriched_chunks = await enrichment.enrich_chunks(
            document_text=document_text,
            chunks=chunks,
            title=title,
            path=path,
            question_focus=profile.hype_question_focus,
            participant_context=participant_context_str,
        )

        # Embed enriched text for all chunks (vector_chunk)
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]
        chunk_vectors = await embedder.embed(enriched_texts)

        # Embed questions based on profile (vector_questions)
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

        # Compute sparse vectors (vector_sparse) -- fallback to None if sidecar unavailable
        sparse_vectors = []
        for ec in enriched_chunks:
            sv = await sparse_embedder.embed_sparse(ec.enriched_text)
            sparse_vectors.append(sv)

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

        enriched_count = sum(1 for ec in enriched_chunks if ec.context_prefix)
        logger.info(
            "Enrichment complete for %s/%s (org=%s, artifact=%s, chunks=%d, enriched=%d, depth=%d, type=%s)",
            kb_slug, path, org_id, artifact_id, len(chunks), enriched_count, synthesis_depth, content_type,
        )

    except Exception as exc:
        logger.error(
            "Enrichment failed for %s/%s (org=%s, artifact=%s): %s",
            kb_slug, path, org_id, artifact_id, exc,
            exc_info=True,
        )
        # Raw vectors remain in Qdrant -- document is still searchable
