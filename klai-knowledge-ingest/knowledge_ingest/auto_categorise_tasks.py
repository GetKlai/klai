"""Procrastinate task registration for taxonomy auto-categorise jobs."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

from knowledge_ingest import queues
from knowledge_ingest.config import settings

logger = structlog.get_logger()


def register_auto_categorise_task(procrastinate_app: Any) -> None:
    """Register the auto-categorise Procrastinate task.

    Called from enrichment_tasks.init_app() alongside other task registrations.
    Retry: 30s -> 5m -> 30m (max 3 retries, SPEC-KB-026 R5).
    """
    import procrastinate

    class _StepwiseRetry(procrastinate.BaseRetryStrategy):
        """Three-step backoff: 30s, 5m, 30m then give up.

        Logs auto_categorise_exhausted at error level only after all retries
        are spent, not on every individual failure.
        """

        _waits: ClassVar[list[int]] = [30, 300, 1800]

        def get_retry_decision(
            self, *, exception: BaseException, job: Any
        ) -> procrastinate.RetryDecision | None:
            if job.attempts >= len(self._waits):
                logger.error(
                    "auto_categorise_exhausted",
                    org_id=job.task_kwargs.get("org_id"),
                    kb_slug=job.task_kwargs.get("kb_slug"),
                    node_id=job.task_kwargs.get("node_id"),
                )
                return None
            return procrastinate.RetryDecision(retry_in={"seconds": self._waits[job.attempts]})

    @procrastinate_app.task(queue=queues.TAXONOMY_BACKFILL, retry=_StepwiseRetry())
    async def run_auto_categorise(
        org_id: str,
        kb_slug: str,
        node_id: int,
        cluster_centroid: list[float] | None = None,
    ) -> dict:
        """Run auto-categorise as a background job with retries."""
        if cluster_centroid is None:
            logger.info(
                "auto_categorise_skipped_no_centroid",
                org_id=org_id,
                kb_slug=kb_slug,
                node_id=node_id,
            )
            return {"status": "skipped", "reason": "no_centroid"}

        try:
            from knowledge_ingest.routes.taxonomy import _auto_categorise_impl

            categorised = await _auto_categorise_impl(
                org_id=org_id,
                kb_slug=kb_slug,
                node_id=node_id,
                cluster_centroid=cluster_centroid,
                threshold=settings.taxonomy_auto_categorise_threshold,
            )
            return {"status": "completed", "categorised": categorised}
        except Exception:
            logger.warning(
                "auto_categorise_attempt_failed",
                org_id=org_id,
                kb_slug=kb_slug,
                node_id=node_id,
            )
            raise

    procrastinate_app.run_auto_categorise = run_auto_categorise  # type: ignore[attr-defined]
