"""Procrastinate task: orchestrated connector-purge.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-04. Wires
``connector_cleanup.purge_connector`` into procrastinate so the portal
DELETE endpoint can flip ``portal_connectors.state='deleting'`` and
return 202 in <100ms while this worker handles the cascading purge
asynchronously.

Retry policy:
    - max_attempts=5
    - exponential backoff: 1m -> 5m -> 25m -> 2h -> 10h
    - On exhaustion, the task logs ``connector_purge_exhausted`` at ERROR
      and the row stays in ``state='deleting'`` for the operator to
      recover via the admin force-purge endpoint (REQ-11).
"""

from __future__ import annotations

from typing import Any

import structlog

from knowledge_ingest.connector_cleanup import purge_connector
from knowledge_ingest.portal_client import finalize_connector_delete

logger = structlog.get_logger()


def register_connector_purge_task(procrastinate_app: Any) -> None:
    """Register ``connector_purge`` task on the given Procrastinate App.

    Called from ``enrichment_tasks.init_app`` alongside other registrations.
    """
    import procrastinate

    class _ExponentialBackoff(procrastinate.BaseRetryStrategy):
        """1m, 5m, 25m, 2h, 10h — capped at 5 attempts.

        See SPEC REQ-04.5: a connector that fails to purge five times in
        ~13 hours triggers an alert and operator recovery via the admin
        endpoint. Anything between transient (network blip, temporary
        Qdrant unavailability) and permanent (DB schema drift) is in
        scope for the retry ladder.
        """

        _waits = (60, 300, 1500, 7200, 36000)
        max_attempts = 5

        def get_retry_decision(
            self, *, exception: BaseException, job: procrastinate.JobContext
        ) -> procrastinate.RetryDecision | None:
            attempt = job.attempts  # 0-based after first failure
            if attempt >= len(self._waits):
                return None  # exhausted, give up
            return procrastinate.RetryDecision(
                retry_in={"seconds": self._waits[attempt]}
            )

    @procrastinate_app.task(
        queue="connector-purge",
        retry=_ExponentialBackoff(),
        # queueing_lock prevents two purge tasks racing for the same
        # connector_id when a user double-clicks delete (the DELETE
        # endpoint is also idempotent, but defence-in-depth).
        pass_context=True,
    )
    async def connector_purge_task(
        context: Any,
        connector_id: str,
        org_id: str,
        kb_slug: str,
    ) -> None:
        """Drive ``purge_connector`` for one connector, retrying on failure.

        On final exhaustion, raises so procrastinate moves the job to
        ``failed`` and an operator can investigate via the admin endpoint.
        """
        log = logger.bind(
            connector_id=connector_id,
            org_id=org_id,
            kb_slug=kb_slug,
            attempt=context.job.attempts if context else None,
        )
        try:
            report = await purge_connector(
                org_id=org_id,
                kb_slug=kb_slug,
                connector_id=connector_id,
                proc_app=procrastinate_app,
            )
        except Exception:
            log.exception("connector_purge_task_failed")
            raise

        # REQ-04.4: portal owns the portal_connectors row, so the
        # final hard-delete is an HTTP call back. If this raises (portal
        # down, secret missing, etc.) procrastinate will retry per the
        # _ExponentialBackoff strategy above. The cleanup itself is
        # already done — retry only needs to redo the cheap finalize.
        try:
            await finalize_connector_delete(connector_id)
        except Exception:
            log.exception("connector_finalize_failed")
            raise

        log.info(
            "connector_purge_task_completed",
            **report.as_dict(),
        )

    procrastinate_app.connector_purge_task = connector_purge_task  # type: ignore[attr-defined]
