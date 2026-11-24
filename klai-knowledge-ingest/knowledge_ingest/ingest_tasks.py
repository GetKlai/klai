"""
Procrastinate task for debounced KB document ingest.

When a Gitea webhook fires, instead of ingesting immediately, a task is
scheduled N minutes in the future (configured via ingest_debounce_seconds).
queueing_lock ensures at most one pending task per document path.

When the task runs it fetches the LATEST content from Gitea (not the
content at queue time), then calls ingest_document(). This means a
10-minute editing session produces exactly one ingest — with the final
version of the document.

Queue: ingest-kb (separate from enrichment queues so it can be tuned
independently; drained by the same Procrastinate worker in app.py).

A page that cannot be ingested has no version row to record a state on: the
ingest fails before it writes one, so the previous version stays in search
untouched. A failed row created up front would carry the new content hash,
and the next save of the same text would be skipped as unchanged. Docs keeps
no per-page sync state either. So the failure is recorded as the error event
``docs_page_ingest_failed`` (org, knowledge base, path, reason), which the
``ingest-docs-page-failed`` alert pages on, and the job itself ends ``failed``
in procrastinate_jobs with the page in its arguments, from where it can be
queued again.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from knowledge_ingest import queues

logger = structlog.get_logger()


def record_docs_page_failure(org_id: str, kb_slug: str, path: str, reason: str) -> None:
    """The one record of a Docs page that is not in search as saved."""
    logger.error(
        "docs_page_ingest_failed", org_id=org_id, kb_slug=kb_slug, path=path, reason=reason
    )


def register_ingest_tasks(procrastinate_app: Any) -> None:
    """Register ingest task on the given Procrastinate App instance."""

    @procrastinate_app.task(queue=queues.INGEST_KB, pass_context=True)
    async def ingest_from_gitea(
        context: Any,
        org_id: str,
        kb_slug: str,
        path: str,
        gitea_repo: str,
        user_id: str | None,
        embedding_deferred_since: int | None = None,
    ) -> None:
        """
        Fetch the current version of a document from Gitea and ingest it.

        Always fetches at execution time — content is never stored in the
        task payload, so the latest version is always used regardless of
        how many saves were made after the task was queued.

        An embedding outage queues the job again every
        ``EMBEDDING_RETRY_SECONDS`` until it has lasted
        ``EMBEDDING_OUTAGE_WINDOW_SECONDS``, as enrichment does; any other
        failure, and an outage past that window, records the page failed.
        """
        # Lazy imports to avoid circular dependencies and psycopg at module level
        from procrastinate.exceptions import AlreadyEnqueued

        from knowledge_ingest.db import tenant_scoped_connection
        from knowledge_ingest.docs_provenance import build_docs_source_extra
        from knowledge_ingest.embedder import (
            EMBEDDING_OUTAGE_WINDOW_SECONDS,
            EMBEDDING_RETRY_SECONDS,
            EmbeddingUnavailable,
        )
        from knowledge_ingest.models import IngestRequest
        from knowledge_ingest.routes.ingest import (
            _fetch_gitea_file,
            ingest_document,
        )

        logger.info("gitea_ingest_started", kb_slug=kb_slug, path=path, org_id=org_id)

        content = await _fetch_gitea_file(gitea_repo, path)
        if content is None:
            logger.warning("gitea_fetch_failed", path=path, repo=gitea_repo)
            return

        req = IngestRequest(
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            content=content,
            source_type="docs",
            content_type="kb_article",
            user_id=user_id,
            extra=build_docs_source_extra(gitea_repo, kb_slug, path),
        )
        try:
            async with tenant_scoped_connection(org_id) as conn:
                result = await ingest_document(conn, req)
        except EmbeddingUnavailable as exc:
            since = embedding_deferred_since or int(time.time())
            if time.time() - since >= EMBEDDING_OUTAGE_WINDOW_SECONDS:
                record_docs_page_failure(org_id, kb_slug, path, "embedding_unavailable")
                raise
            try:
                await ingest_from_gitea.configure(
                    queueing_lock=context.job.queueing_lock,
                    schedule_in={"seconds": EMBEDDING_RETRY_SECONDS},
                ).defer_async(
                    org_id=org_id,
                    kb_slug=kb_slug,
                    path=path,
                    gitea_repo=gitea_repo,
                    user_id=user_id,
                    embedding_deferred_since=since,
                )
            except AlreadyEnqueued:
                # A newer save is already queued and fetches the latest text.
                pass
            logger.warning(
                "gitea_ingest_deferred_embedding_unavailable",
                kb_slug=kb_slug,
                path=path,
                org_id=org_id,
                deferred_since=since,
                error=str(exc),
            )
            return
        except Exception as exc:
            record_docs_page_failure(org_id, kb_slug, path, type(exc).__name__)
            raise
        logger.info(
            "gitea_ingest_complete",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            status=result.get("status"),
        )

    # Expose via app attribute (same pattern as enrichment_tasks)
    procrastinate_app.ingest_from_gitea = ingest_from_gitea  # type: ignore[attr-defined]
