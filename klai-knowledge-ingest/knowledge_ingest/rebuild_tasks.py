"""
Operator-triggered KB rebuild task (SPEC-RAG-REBUILD-KB-001).

Brings ALL existing artifacts in a KB up to the current pipeline by
re-running chunking, enrichment, embedding, and Qdrant upsert for every
active artifact.

When to use
-----------
After deploying pipeline changes that alter how chunks are produced:
  - SPEC-RAG-CONTEXTUAL-001 (merged): document_summary-driven context prefix.
  - SPEC-RAG-PARENT-CHILD-001 (merged): child + parent chunk pairs stored in
    ``knowledge.parent_chunks``.

Idempotency
-----------
Re-running with unchanged content yields the same output. The LiteLLM proxy
may cache summary generation; chunking and enrichment are deterministic per
input. Qdrant upsert uses delete-then-insert per path — no orphans.

Queues
------
REBUILD_KB (LLM lane) — many per-artifact LLM calls; bounded by upstream
rate limit via semaphore inside ``_rebuild_artifact``.

Concurrency
-----------
A bounded asyncio.Semaphore (default: 4) throttles concurrent per-artifact
rebuilds to keep LiteLLM happy under the daily token budget.

Queueing lock
-------------
``queueing_lock=f"rebuild-kb-{org_id}-{kb_slug}"`` ensures concurrent
rebuild tasks for the same KB are blocked (``AlreadyEnqueued`` raised at
defer time — caller should surface this as a 409-equivalent).

Source text (v1 scope)
----------------------
Reads document text from ``knowledge.artifacts.extra->>'document_text'``.
Artifacts without this field are skipped and counted in ``artifacts_skipped``
with a ``rebuild_skip_no_text`` log event.

OPEN QUESTION: ``document_text`` is not currently stored on
``knowledge.artifacts.extra`` by the default ingest pipeline. Most existing
artifacts will have ``document_text`` absent and will be skipped. A follow-up
SPEC should either:
  a) store ``document_text`` in ``extra`` during initial ingest, or
  b) provide a per-connector re-fetch adapter (more complex, out of v1 scope).
Until that lands, this task is primarily useful for artifacts where the
connector explicitly wrote ``document_text`` into extra (e.g. KB connector
direct uploads, some Notion pages where content was persisted for future
reprocessing).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from knowledge_ingest import queues

logger = structlog.get_logger()

# Maximum concurrent per-artifact rebuilds — keeps LiteLLM rate-limit happy.
_SEMAPHORE_LIMIT = 4

# Sentinel value for "still active" belief_time_end (mirrors pg_store._SENTINEL).
_SENTINEL = 253402300800


def register_rebuild_tasks(procrastinate_app: Any) -> None:
    """Register the rebuild_kb Procrastinate task on the given App instance.

    Called from ``enrichment_tasks.init_app()`` alongside other task
    registrations. No automatic retry — operators retrigger manually after
    fixing the root cause. Idempotency ensures a re-run is always safe.
    """
    import procrastinate

    # @MX:ANCHOR: public Procrastinate task — queueing_lock guarantees at most
    # @MX:REASON: one concurrent rebuild per (org_id, kb_slug) pair; callers
    #             in tests and the runbook both rely on AlreadyEnqueued being
    #             raised on duplicate defer.
    @procrastinate_app.task(
        queue=queues.REBUILD_KB,
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def rebuild_kb(org_id: str, kb_slug: str) -> dict:
        """Rebuild all active artifacts in a KB against the current pipeline.

        Iterates every active artifact in (org_id, kb_slug); for each:
          1. Reads document_text from extra JSONB — skips if absent.
          2. Re-chunks with chunk_markdown_with_parents.
          3. Re-runs _enrich_document (enrichment + TEI + sparse embedding +
             Qdrant delete-then-upsert).
          4. Replaces parent_chunks rows in PostgreSQL.

        Returns ``{org_id, kb_slug, artifacts_processed, artifacts_skipped,
                   artifacts_failed, duration_ms}``.
        """
        return await _rebuild_kb_core(org_id=org_id, kb_slug=kb_slug)

    procrastinate_app.rebuild_kb = rebuild_kb  # type: ignore[attr-defined]


async def rebuild_kb_inline(org_id: str, kb_slug: str) -> dict:
    """Inline variant for operator runbook use (no Procrastinate queue).

    Calls the same core logic as the Procrastinate task synchronously.
    Useful for docker exec one-liners where you want direct output.

    Example::

        import asyncio
        from knowledge_ingest.rebuild_tasks import rebuild_kb_inline
        asyncio.run(rebuild_kb_inline("<org_zitadel_id>", "<kb_slug>"))
    """
    return await _rebuild_kb_core(org_id=org_id, kb_slug=kb_slug)


async def _list_active_artifacts(org_id: str, kb_slug: str) -> list[dict]:
    """Return all currently-active artifact rows for a KB.

    Selects only rows with belief_time_end == _SENTINEL (still active).
    """
    from knowledge_ingest.db import get_pool

    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, path, content_type, extra, synthesis_depth,
               belief_time_start, belief_time_end, user_id
        FROM knowledge.artifacts
        WHERE org_id = $1
          AND kb_slug = $2
          AND belief_time_end = $3
        ORDER BY created_at
        """,
        org_id,
        kb_slug,
        _SENTINEL,
    )
    return [dict(r) for r in rows]


async def _rebuild_artifact(
    *,
    org_id: str,
    kb_slug: str,
    artifact: dict,
    semaphore: asyncio.Semaphore,
) -> str:
    """Rebuild one artifact. Returns 'processed', 'skipped', or 'failed'.

    Fail-open: exceptions are caught, logged, and counted as 'failed'
    so that remaining artifacts in the KB continue processing.
    """
    artifact_id = str(artifact["id"])
    path: str = artifact.get("path") or artifact_id

    async with semaphore:
        try:
            # Parse extra JSONB. asyncpg may return it as str or dict.
            raw_extra = artifact.get("extra")
            extra: dict = {}
            if raw_extra:
                extra = json.loads(raw_extra) if isinstance(raw_extra, str) else dict(raw_extra)

            document_text: str | None = extra.get("document_text")
            if not document_text:
                logger.info(
                    "rebuild_skip_no_text",
                    org_id=org_id,
                    kb_slug=kb_slug,
                    artifact_id=artifact_id,
                    path=path,
                )
                return "skipped"

            content_type: str = artifact.get("content_type") or "unknown"
            synthesis_depth: int = artifact.get("synthesis_depth") or 0
            user_id: str | None = artifact.get("user_id")
            belief_time_start: int | None = artifact.get("belief_time_start")
            belief_time_end: int | None = artifact.get("belief_time_end")
            title = path

            # Step 1: Re-chunk with parent-child chunking (SPEC-RAG-PARENT-CHILD-001).
            from knowledge_ingest import chunker

            child_chunks, parent_chunks_obj = chunker.chunk_markdown_with_parents(document_text)
            child_texts = [c.text for c in child_chunks]

            if not child_texts:
                logger.info(
                    "rebuild_skip_no_chunks",
                    org_id=org_id,
                    kb_slug=kb_slug,
                    artifact_id=artifact_id,
                    path=path,
                )
                return "skipped"

            # Serialise ParentChunk dataclasses to the dict shape pg_store expects.
            parents_serialised: list[dict] = [
                {
                    "text": p.text,
                    "token_count": chunker._approx_token_count(p.text),
                    "position": p.position,
                }
                for p in parent_chunks_obj
            ]

            # Build extra_payload matching the shape expected by _enrich_document.
            # Carry through all original metadata so Qdrant payload stays consistent.
            extra_payload: dict = dict(extra)
            extra_payload.setdefault("belief_time_start", belief_time_start)
            extra_payload.setdefault("belief_time_end", belief_time_end)

            # Steps 2-3: enrichment + TEI/sparse embedding + Qdrant delete-then-upsert.
            # _enrich_document handles all of this atomically for a single document.
            from knowledge_ingest.enrichment_tasks import _enrich_document

            await _enrich_document(
                org_id=org_id,
                kb_slug=kb_slug,
                path=path,
                document_text=document_text,
                chunks=child_texts,
                title=title,
                artifact_id=artifact_id,
                user_id=user_id,
                extra_payload=extra_payload,
                synthesis_depth=synthesis_depth,
                content_type=content_type,
            )

            # Step 4: Replace parent_chunks rows in PostgreSQL.
            from knowledge_ingest import pg_store

            await pg_store.delete_parent_chunks_for_artifact(artifact_id)
            if parents_serialised:
                await pg_store.insert_parent_chunks(
                    artifact_id=artifact_id,
                    org_id=org_id,
                    parents=parents_serialised,
                )

            logger.info(
                "rebuild_artifact_processed",
                org_id=org_id,
                kb_slug=kb_slug,
                artifact_id=artifact_id,
                path=path,
                children=len(child_texts),
                parents=len(parents_serialised),
            )
            return "processed"

        except Exception:
            logger.exception(
                "rebuild_artifact_failed",
                org_id=org_id,
                kb_slug=kb_slug,
                artifact_id=artifact_id,
                path=path,
            )
            return "failed"


async def _rebuild_kb_core(org_id: str, kb_slug: str) -> dict:
    """Core rebuild logic shared by the Procrastinate task and rebuild_kb_inline."""
    t_start = time.monotonic()
    logger.info("rebuild_kb_started", org_id=org_id, kb_slug=kb_slug)

    artifacts = await _list_active_artifacts(org_id=org_id, kb_slug=kb_slug)
    total = len(artifacts)
    logger.info(
        "rebuild_kb_artifacts_found",
        org_id=org_id,
        kb_slug=kb_slug,
        total=total,
    )

    semaphore = asyncio.Semaphore(_SEMAPHORE_LIMIT)

    # Process all artifacts concurrently, bounded by the semaphore.
    # asyncio.gather propagates each artifact's outcome independently.
    outcomes = await asyncio.gather(
        *(
            _rebuild_artifact(
                org_id=org_id,
                kb_slug=kb_slug,
                artifact=artifact,
                semaphore=semaphore,
            )
            for artifact in artifacts
        )
    )

    processed = outcomes.count("processed")
    skipped = outcomes.count("skipped")
    failed = outcomes.count("failed")
    duration_ms = int((time.monotonic() - t_start) * 1000)

    result: dict = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "artifacts_processed": processed,
        "artifacts_skipped": skipped,
        "artifacts_failed": failed,
        "duration_ms": duration_ms,
    }

    logger.info("rebuild_kb_completed", **result)
    return result
