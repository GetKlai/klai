"""
Operator-triggered KB rebuild task (SPEC-RAG-REBUILD-KB-001).

Run it as ``python -m knowledge_ingest.rebuild_tasks --org <org> --kb <kb>``:
that previews (counts, first documents, model calls) and writes nothing.
``--execute`` writes and needs ``INGEST_OK=1``.

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
input. Qdrant writes replace the points of each path — no orphans.

Queues
------
REBUILD_KB (LLM lane) — many per-artifact LLM calls; bounded by upstream
rate limit via semaphore inside ``_rebuild_artifact``.

Concurrency
-----------
A bounded asyncio.Semaphore (default: 4) throttles concurrent per-artifact
rebuilds to keep LiteLLM happy under the daily token budget.

Guards
------
A write run needs ``INGEST_OK=1`` and holds the advisory lock of its knowledge
base (``operations.kb_operation``), so a second run for the same KB is refused
with ``OperationBusy``. Both live in ``_rebuild_kb_core``, which the inline
function, the command line and the Procrastinate task all pass through.

Source text
-----------
Reads document text from ``knowledge.artifacts.extra->>'document_text'``.
PR #347 (SPEC-RAG-CONTEXTUAL-001) made the ingest route persist
``document_text`` on ``extra`` for every fresh ingest, so re-ingests
after that change rebuild without any reconstruction.

Artifacts ingested before PR #347 will not have ``document_text`` on
``extra``. For those, ``_reconstruct_document_text`` rebuilds the body
by concatenating the existing Qdrant chunks for the artifact's path in
chunk_index order. The reconstruction is lossy — frontmatter is dropped
and chunk overlap leaves duplication on boundaries — but it is enough
material to feed the new chunker + summary generator. Artifacts where
neither path produces text (no extra.document_text AND no Qdrant chunks)
are skipped with a ``rebuild_skip_no_text`` log event and counted in
``artifacts_skipped``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from knowledge_ingest import queues
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.operations import guard_cli, kb_operation, require_ingest_ok

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

    # @MX:ANCHOR: public Procrastinate task. Nothing in the service defers it;
    # @MX:REASON: whoever does still passes through _rebuild_kb_core, which
    #             refuses a run without INGEST_OK=1 and a second run on the same KB.
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
             Qdrant replace of the path's points).
          4. Replaces parent_chunks rows in PostgreSQL.

        Returns ``{org_id, kb_slug, artifacts_processed, artifacts_skipped,
                   artifacts_failed, duration_ms}``.
        """
        return await _rebuild_kb_core(org_id=org_id, kb_slug=kb_slug, execute=True)

    procrastinate_app.rebuild_kb = rebuild_kb  # type: ignore[attr-defined]


async def rebuild_kb_inline(org_id: str, kb_slug: str, *, execute: bool = False) -> dict:
    """Inline variant for operator runbook use (no Procrastinate queue).

    Calls the same core logic as the Procrastinate task. Without ``execute`` it
    only previews; with it the run needs ``INGEST_OK=1``.
    """
    return await _rebuild_kb_core(org_id=org_id, kb_slug=kb_slug, execute=execute)


async def _reconstruct_document_text(
    org_id: str,
    kb_slug: str,
    path: str,
) -> str | None:
    """Reconstruct a document body from its existing Qdrant chunks.

    Used by the rebuild path when the legacy artifact row has no
    ``document_text`` on its extra JSONB. We pull every chunk for
    ``(org_id, kb_slug, path)`` from Qdrant in chunk_index order, then
    concat the (non-enriched) text values with double-newline separators.

    The reconstruction is lossy — markdown frontmatter is dropped at
    ingest time and chunk overlap leaves duplication on boundaries.
    Good enough to feed the summary generator + new chunker; not the
    same as a fresh re-fetch from the source connector.

    Returns None when no chunks are found.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from knowledge_ingest import qdrant_store

    client = qdrant_store.get_client()
    try:
        scroll_result, _ = await client.scroll(
            collection_name=qdrant_store.COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                    FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                    FieldCondition(key="path", match=MatchValue(value=path)),
                ]
            ),
            limit=512,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        logger.warning(
            "rebuild_qdrant_scroll_failed",
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            error=str(exc)[:200],
        )
        return None

    if not scroll_result:
        return None

    # Sort by chunk_index when present; fall back to insertion order.
    def _idx(point):
        try:
            return int(point.payload.get("chunk_index", 0))
        except Exception:
            return 0

    sorted_points = sorted(scroll_result, key=_idx)
    parts: list[str] = []
    for p in sorted_points:
        text = p.payload.get("text") or ""
        if text:
            parts.append(text)
    if not parts:
        return None
    return "\n\n".join(parts)


async def _list_active_artifacts(org_id: str, kb_slug: str) -> list[dict]:
    """Return all currently-active artifact rows for a KB.

    Selects only rows with belief_time_end == _SENTINEL (still active).
    SPEC-TI-003 AC-9: tenant_scoped_connection sets RLS GUC before the SELECT.
    """
    # @MX:NOTE: tenant_scoped_connection required so RLS policy sees GUC
    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
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


def _chunk_document(document_text: str, content_type: str):
    """Normalise and cut a document the way a rebuild does; no model call."""
    from knowledge_ingest import chunker
    from knowledge_ingest.content_profiles import child_chunk_chars
    from knowledge_ingest.document_normalizer import normalize_document_for_chunking

    document_text = normalize_document_for_chunking(document_text)
    children, parents = chunker.chunk_markdown_with_parents(
        document_text, child_size=child_chunk_chars(content_type)
    )
    return document_text, children, parents


def _extra_of(artifact: dict) -> dict:
    raw = artifact.get("extra")
    if not raw:
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


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
            extra = _extra_of(artifact)

            document_text: str | None = extra.get("document_text")
            if not document_text:
                # Fallback: reconstruct document_text from existing Qdrant chunks.
                # Concat the original chunk text in chunk_index order. Loses
                # frontmatter and exact whitespace but is enough material for
                # the chunker + summary-generator to produce a usable rebuild.
                # This is the v1 path for legacy artifacts ingested before we
                # started persisting document_text on extra; new ingests can
                # skip the reconstruction once the storage gap is closed.
                document_text = await _reconstruct_document_text(
                    org_id=org_id,
                    kb_slug=kb_slug,
                    path=path,
                )
                if not document_text:
                    logger.info(
                        "rebuild_skip_no_text",
                        org_id=org_id,
                        kb_slug=kb_slug,
                        artifact_id=artifact_id,
                        path=path,
                    )
                    return "skipped"
                logger.info(
                    "rebuild_text_reconstructed_from_qdrant",
                    org_id=org_id,
                    kb_slug=kb_slug,
                    artifact_id=artifact_id,
                    path=path,
                    chars=len(document_text),
                )

            content_type: str = artifact.get("content_type") or "unknown"
            synthesis_depth: int = artifact.get("synthesis_depth") or 0
            user_id: str | None = artifact.get("user_id")
            belief_time_start: int | None = artifact.get("belief_time_start")
            belief_time_end: int | None = artifact.get("belief_time_end")
            title = path

            # Step 1: Re-chunk with parent-child chunking (SPEC-RAG-PARENT-CHILD-001).
            from knowledge_ingest import chunker

            document_text, child_chunks, parent_chunks_obj = _chunk_document(
                document_text, content_type
            )
            child_texts = [c.text for c in child_chunks]
            # Each child knows its parent's index in parent_chunks_obj —
            # _enrich_document needs this list to thread parent_chunks.id
            # into each child's Qdrant payload (parent_chunk_id).
            parent_index_per_child: list[int | None] = [c.parent_index for c in child_chunks]
            heading_path_per_child: list[str] = [c.heading_path for c in child_chunks]

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
                    "heading_path": p.heading_path,
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

            # SPEC-RAG-PARENT-CHILD-001: clear stale parent_chunks rows before
            # _enrich_document re-inserts them. Without this, the prior
            # rebuild's parent rows pile up and child→parent lookup at retrieval
            # time hits the wrong target.
            # SPEC-TI-003-FOLLOWUP-001 AC-1: pin RLS GUC on org_id for the
            # delete; _enrich_document opens its own tsc internally.
            from knowledge_ingest import pg_store as _pg_store

            # The rebuild redoes the enrichment: the document says pending
            # until it ends, and failed when it does not, like a job would.
            from knowledge_ingest.derived_state import FAILED, PENDING, set_product_state

            async with tenant_scoped_connection(org_id) as conn:
                await set_product_state(conn, artifact_id, "enrichment", PENDING)
                await _pg_store.delete_parent_chunks_for_artifact(conn, artifact_id)

            # Steps 2-3: enrichment + TEI/sparse embedding + Qdrant
            # replace of the path's points. _enrich_document inserts parent_chunks rows
            # itself (so it can thread the generated row ids into each
            # child's Qdrant payload as ``parent_chunk_id``) — passing
            # ``parents`` + ``parent_index_per_child`` here is what lights up
            # the parent-child retrieval expansion in retrieval-api. Before
            # this fix the rebuild path called ``_enrich_document`` without
            # them, so every child chunk had ``parent_chunk_id=None``
            # and parent expansion silently degraded to chunk-text-only.
            from procrastinate.exceptions import JobAborted

            from knowledge_ingest.enrichment_tasks import _enrich_document, _finish

            try:
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
                    parents=parents_serialised,
                    parent_index_per_child=parent_index_per_child,
                    heading_path_per_child=heading_path_per_child,
                )
            except JobAborted:
                raise  # the version or its connector went away: no state
            except Exception:
                await _finish("enrichment", artifact_id, FAILED, "rebuild_failed")
                raise

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


_PREVIEW_DOCUMENTS = 10


async def _preview(org_id: str, kb_slug: str) -> dict:
    """Count what a rebuild would touch and show the first documents; writes nothing.

    A model call is one summary per document plus one per chunk. Documents
    without stored text are rebuilt from their Qdrant chunks, so their chunk
    count is only known at run time and is left out of the estimate.
    """
    artifacts = await _list_active_artifacts(org_id=org_id, kb_slug=kb_slug)
    first: list[dict] = []
    with_text = 0
    chunks = 0
    for artifact in artifacts:
        document_text = _extra_of(artifact).get("document_text")
        if not document_text:
            continue
        with_text += 1
        _, children, parents = _chunk_document(
            document_text, artifact.get("content_type") or "unknown"
        )
        chunks += len(children)
        if len(first) < _PREVIEW_DOCUMENTS:
            first.append(
                {"path": artifact.get("path"), "children": len(children), "parents": len(parents)}
            )
    return {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "dry_run": True,
        "artifacts": len(artifacts),
        "without_stored_text": len(artifacts) - with_text,
        "chunks_with_stored_text": chunks,
        "model_calls_estimate": with_text + chunks,
        "first": first,
    }


async def _rebuild_kb_core(org_id: str, kb_slug: str, *, execute: bool) -> dict:
    """Core rebuild logic shared by the Procrastinate task and rebuild_kb_inline."""
    if not execute:
        return await _preview(org_id, kb_slug)
    require_ingest_ok("rebuild_kb")
    async with kb_operation(org_id, [kb_slug], execute=True):
        return await _rebuild_kb_locked(org_id, kb_slug)


async def _rebuild_kb_locked(org_id: str, kb_slug: str) -> dict:
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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Preview or run a rebuild of one knowledge base")
    parser.add_argument("--org", required=True, help="org_id (zitadel id)")
    parser.add_argument("--kb", required=True, help="knowledge base slug")
    parser.add_argument("--execute", action="store_true", help="rebuild; preview without it")
    args = parser.parse_args()
    guard_cli("rebuild_kb", args.execute)
    print(asyncio.run(rebuild_kb_inline(args.org, args.kb, execute=args.execute)))


if __name__ == "__main__":
    main()
