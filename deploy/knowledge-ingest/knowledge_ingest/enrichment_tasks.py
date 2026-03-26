"""
Procrastinate task definitions for async chunk enrichment.

Two queues:
  enrich-interactive  — single-doc uploads, drains first (higher priority)
  enrich-bulk         — crawl/import jobs

Both tasks call _enrich_document() which:
1. Calls enrichment.enrich_chunks() with semaphore (max ENRICHMENT_MAX_CONCURRENT concurrent LLM calls)
2. Embeds enriched_text as vector_chunk for all chunks
3. If synthesis_depth <= 1: concatenates all questions and embeds as vector_questions
4. Upserts enriched vectors + payload to Qdrant (overwrites raw chunk points)

Procrastinate is imported lazily (inside init_app) so this module can be imported
in test environments where psycopg/libpq is not available.
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge_ingest import embedder, enrichment, qdrant_store

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
    ) -> None:
        """Enrich chunks for a single-doc upload (high priority)."""
        await _enrich_document(
            org_id, kb_slug, path, document_text, chunks, title,
            artifact_id, user_id, extra_payload, synthesis_depth,
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
    ) -> None:
        """Enrich chunks for crawl/import jobs (lower priority)."""
        await _enrich_document(
            org_id, kb_slug, path, document_text, chunks, title,
            artifact_id, user_id, extra_payload, synthesis_depth,
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
) -> None:
    """
    Core enrichment logic shared by both task variants.
    Errors are logged but do not raise — raw vectors remain in Qdrant.
    """
    try:
        enriched_chunks = await enrichment.enrich_chunks(
            document_text=document_text,
            chunks=chunks,
            title=title,
            path=path,
        )

        # Embed enriched text for all chunks (vector_chunk)
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]
        chunk_vectors = await embedder.embed(enriched_texts)

        # Embed aggregated questions for depth 0-1 chunks (vector_questions)
        question_vectors: list[list[float] | None]
        if synthesis_depth <= 1:
            question_strings = [
                " ".join(ec.questions) if ec.questions else ec.original_text
                for ec in enriched_chunks
            ]
            question_vectors = await embedder.embed(question_strings)
        else:
            question_vectors = [None] * len(enriched_chunks)

        await qdrant_store.upsert_enriched_chunks(
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            enriched_chunks=enriched_chunks,
            chunk_vectors=chunk_vectors,
            question_vectors=question_vectors,
            artifact_id=artifact_id,
            extra_payload=extra_payload,
            user_id=user_id,
        )

        enriched_count = sum(1 for ec in enriched_chunks if ec.context_prefix)
        logger.info(
            "Enrichment complete for %s/%s (org=%s, artifact=%s, chunks=%d, enriched=%d, depth=%d)",
            kb_slug, path, org_id, artifact_id, len(chunks), enriched_count, synthesis_depth,
        )

    except Exception as exc:
        logger.error(
            "Enrichment failed for %s/%s (org=%s, artifact=%s): %s",
            kb_slug, path, org_id, artifact_id, exc,
            exc_info=True,
        )
        # Raw vectors remain in Qdrant — document is still searchable
