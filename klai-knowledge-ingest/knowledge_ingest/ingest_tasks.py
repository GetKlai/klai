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
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger()


def register_ingest_tasks(procrastinate_app: object) -> None:
    """Register ingest task on the given Procrastinate App instance."""

    @procrastinate_app.task(queue="ingest-kb")  # type: ignore[misc]
    async def ingest_from_gitea(
        org_id: str,
        kb_slug: str,
        path: str,
        gitea_repo: str,
        user_id: str | None,
    ) -> None:
        """
        Fetch the current version of a document from Gitea and ingest it.

        Always fetches at execution time — content is never stored in the
        task payload, so the latest version is always used regardless of
        how many saves were made after the task was queued.
        """
        # Lazy imports to avoid circular dependencies and psycopg at module level
        from knowledge_ingest.routes.ingest import (  # noqa: PLC0415
            _fetch_gitea_file,
            ingest_document,
        )
        from knowledge_ingest.models import IngestRequest  # noqa: PLC0415

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
        )
        result = await ingest_document(req)
        logger.info(
            "gitea_ingest_complete",
            kb_slug=kb_slug,
            path=path,
            org_id=org_id,
            status=result.get("status"),
        )

    # Expose via app attribute (same pattern as enrichment_tasks)
    procrastinate_app.ingest_from_gitea = ingest_from_gitea  # type: ignore[attr-defined]
