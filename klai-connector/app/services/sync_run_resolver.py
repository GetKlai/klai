"""SPEC-CRAWLER-006: live status resolver for delegated web_crawler runs.

The connector's :class:`SyncEngine` no longer polls knowledge-ingest after
enqueue. Instead, ``sync_runs`` rows for web_crawler connectors stay in
``RUNNING`` state with ``cursor_state.remote_job_id`` set, and the resolver
fetches live progress from knowledge-ingest at read time.

When the remote job has reached a terminal state (completed/failed), the
resolver writes the final state back to the local row exactly once and
emits the portal callback. Subsequent reads see a closed row and short-
circuit (no upstream call).

Caching: per ``remote_job_id``, 30 s TTL. Two concurrent reads for the
same job_id may miss the cache simultaneously and both hit upstream — this
is rare and correct; the second read updates the cache on completion.

Failure mode: when knowledge-ingest is unreachable, the resolver returns
the local row unchanged with ``live_resolution_failed=True`` so the UI
can render "Bezig (status onbekend)" instead of crashing the whole list.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.knowledge_ingest import CrawlSyncClient
from app.core.database import tenant_scoped_session
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.services.crawl_sync_status import (
    is_completed_remote_crawl_status,
    is_terminal_remote_crawl_status,
    remote_crawl_failure_error,
)
from app.services.portal_client import PortalClient

logger = get_logger(__name__)


# Cache TTL — REQ-CRAWLER-006-05. Long enough to absorb a UI list view
# polling every 5 s; short enough that terminal transitions surface
# within one cache window.
_CACHE_TTL_S: float = 30.0

# Cache GC threshold — entries older than this are dropped on the next
# write. 10x TTL keeps fresh entries safe from premature eviction while
# preventing unbounded growth from terminalised runs whose entries the
# resolver never reads again.
_CACHE_GC_S: float = _CACHE_TTL_S * 10


@dataclass(slots=True)
class _CacheEntry:
    fetched_at: float
    payload: dict[str, Any]


@dataclass(slots=True)
class ResolvedSyncRun:
    """Shape returned to the route layer.

    Mirrors :class:`app.schemas.sync.SyncRunResponse` plus the live
    progress fields. The route maps these onto the response model;
    Pydantic's ``from_attributes=True`` reads matching attribute names.
    """

    id: uuid.UUID
    connector_id: uuid.UUID
    status: str
    started_at: datetime
    completed_at: datetime | None
    documents_total: int
    documents_ok: int
    documents_failed: int
    bytes_processed: int
    error_details: list[dict[str, Any]] | None
    pages_done: int | None
    pages_total: int | None
    live_resolution_failed: bool


class SyncRunResolver:
    """Resolves live state for delegated web_crawler sync_runs.

    A single instance lives for the process lifetime, attached to
    ``app.state.sync_run_resolver`` at startup.
    """

    def __init__(
        self,
        *,
        crawl_sync_client: CrawlSyncClient,
        session_maker: async_sessionmaker[AsyncSession],
        portal_client: PortalClient,
    ) -> None:
        self._crawl_sync_client = crawl_sync_client
        self._session_maker = session_maker
        self._portal_client = portal_client
        self._cache: dict[str, _CacheEntry] = {}

    async def resolve(self, sync_run: SyncRun) -> ResolvedSyncRun:
        """Return a sync_run snapshot with live progress when applicable.

        Decision: a sync_run needs live resolution iff
        ``status == RUNNING`` AND ``cursor_state.remote_job_id`` is set.
        That marker is uniquely set by SPEC-CRAWLER-006's fire-and-forget
        delegation; other connector types write terminal state inline and
        never leave a remote_job_id on a RUNNING row.
        """
        remote_job_id = self._extract_remote_job_id(sync_run)
        if sync_run.status != SyncStatus.RUNNING or remote_job_id is None:
            return self._snapshot(sync_run, pages_done=None, pages_total=None, live_resolution_failed=False)

        try:
            live = await self._fetch_status(remote_job_id)
        except httpx.HTTPError as exc:
            logger.warning(
                "sync_run_resolver_upstream_unreachable",
                extra={
                    "event": "sync_run_resolver_upstream_unreachable",
                    "remote_job_id": remote_job_id,
                    "sync_run_id": str(sync_run.id),
                    "error": str(exc),
                },
            )
            return self._snapshot(sync_run, pages_done=None, pages_total=None, live_resolution_failed=True)

        live_status = str(live.get("status", "running"))
        if is_terminal_remote_crawl_status(live_status):
            sync_run = await self._finalize(sync_run, live)
            return self._snapshot(
                sync_run,
                pages_done=live.get("pages_done"),
                pages_total=live.get("pages_total"),
                live_resolution_failed=False,
            )

        # Still running — surface live counts, don't mutate the row.
        return self._snapshot(
            sync_run,
            pages_done=live.get("pages_done"),
            pages_total=live.get("pages_total"),
            live_resolution_failed=False,
        )

    @staticmethod
    def _extract_remote_job_id(sync_run: SyncRun) -> str | None:
        cursor = sync_run.cursor_state
        if not isinstance(cursor, dict):
            return None
        rid = cursor.get("remote_job_id")
        return str(rid) if rid else None

    async def _fetch_status(self, remote_job_id: str) -> dict[str, Any]:
        """Cached fetch of the live status. 30 s TTL per job_id.

        On cache hit returns the cached payload. On expiry/miss calls
        knowledge-ingest and refreshes. Exceptions propagate so the
        caller can render a degraded snapshot.

        Cache size is bounded by sweeping entries older than
        ``_CACHE_GC_S`` on every miss. Without this the cache
        accumulates entries for terminalised runs that the resolver
        never reads again — small per entry but unbounded over the
        process lifetime of a long-running connector container.
        """
        now = time.monotonic()
        entry = self._cache.get(remote_job_id)
        if entry is not None and (now - entry.fetched_at) < _CACHE_TTL_S:
            return entry.payload

        payload = await self._crawl_sync_client.crawl_sync_status(remote_job_id)
        self._gc_cache(now=now)
        self._cache[remote_job_id] = _CacheEntry(fetched_at=now, payload=payload)
        return payload

    def _gc_cache(self, *, now: float) -> None:
        """Drop cache entries older than ``_CACHE_GC_S`` seconds.

        Runs synchronously inside ``_fetch_status`` (the only writer)
        so no separate task or lock is needed. Worst case: one O(n)
        walk per upstream call. n is bounded by activity in the last
        GC window.
        """
        cutoff = now - _CACHE_GC_S
        self._cache = {k: v for k, v in self._cache.items() if v.fetched_at >= cutoff}

    async def _finalize(self, sync_run: SyncRun, live: dict[str, Any]) -> SyncRun:
        """Write terminal state to the local row and notify portal.

        Idempotent: a concurrent reader that already finalized leaves
        the row in a terminal state on commit; the late writer's update
        is a no-op overwrite of identical values.
        """
        live_status = str(live.get("status"))
        pages_total = int(live.get("pages_total") or 0)
        pages_done = int(live.get("pages_done") or 0)
        if is_completed_remote_crawl_status(live_status):
            new_status = SyncStatus.COMPLETED
            documents_total = pages_total
            documents_ok = pages_done
            documents_failed = 0
            error_details: list[dict[str, Any]] | None = None
            quality_status: str | None = "healthy"
        else:
            new_status = SyncStatus.FAILED
            documents_total = pages_total
            documents_ok = pages_done
            documents_failed = max(0, pages_total - pages_done)
            remote_error = remote_crawl_failure_error(live)
            error_details = [
                {
                    "error": remote_error,
                    "service": "knowledge-ingest",
                    "remote_job_id": self._extract_remote_job_id(sync_run),
                },
            ]
            quality_status = None

        completed_at = datetime.now(UTC)
        # Use tenant_scoped_session so the UPDATE passes the WITH CHECK
        # constraint (org_id = _rls_current_org_id()). SPEC-TI-002.
        assert sync_run.org_id is not None, "Cannot finalise a sync_run with no org_id"
        async with tenant_scoped_session(sync_run.org_id) as session:
            # SELECT ... FOR UPDATE serialises concurrent finalisers on
            # the same sync_run. Two readers that both see status=RUNNING
            # would otherwise both commit a terminal transition AND both
            # call ``report_sync_status`` — at-most-twice instead of the
            # SPEC's exactly-once contract. with_for_update blocks the
            # second reader until the first commits; the second then
            # observes status != RUNNING and short-circuits below.
            row = await session.get(SyncRun, sync_run.id, with_for_update=True)
            if row is None:
                logger.warning(
                    "sync_run_resolver_row_disappeared",
                    extra={"sync_run_id": str(sync_run.id)},
                )
                return sync_run
            # Race-safety: another reader may have terminalized first.
            if row.status != SyncStatus.RUNNING:
                return row

            row.status = new_status
            row.completed_at = completed_at
            row.documents_total = documents_total
            row.documents_ok = documents_ok
            row.documents_failed = documents_failed
            row.error_details = error_details
            row.quality_status = quality_status
            existing_cursor = row.cursor_state if isinstance(row.cursor_state, dict) else {}
            row.cursor_state = {
                **existing_cursor,
                "remote_status": live_status,
            }
            await session.commit()
            # No session.refresh after commit — expire_on_commit=False
            # keeps the in-memory attribute writes intact, and a refresh
            # would open a fresh implicit transaction that could trip
            # RLS guards on category-D tables (see klai-portal
            # portal-backend.md "Post-commit db.refresh on RLS tables").

        # Best-effort callback to portal so the connector's last_sync_*
        # fields update. SPEC says this happens exactly once per run; the
        # row.status check above guarantees idempotence.
        await self._portal_client.report_sync_status(
            connector_id=row.connector_id,
            sync_run_id=row.id,
            sync_status=new_status,
            completed_at=completed_at,
            documents_total=documents_total,
            documents_ok=documents_ok,
            documents_failed=documents_failed,
            bytes_processed=0,
            error_details=error_details,
        )
        logger.info(
            "sync_run_resolver_terminalized",
            extra={
                "event": "sync_complete",
                "sync_run_id": str(row.id),
                "connector_id": str(row.connector_id),
                "status": new_status,
                "documents_ok": documents_ok,
                "documents_total": documents_total,
            },
        )
        return row

    @staticmethod
    def _snapshot(
        sync_run: SyncRun,
        *,
        pages_done: int | None,
        pages_total: int | None,
        live_resolution_failed: bool,
    ) -> ResolvedSyncRun:
        return ResolvedSyncRun(
            id=sync_run.id,
            connector_id=sync_run.connector_id,
            status=sync_run.status,
            started_at=sync_run.started_at,
            completed_at=sync_run.completed_at,
            documents_total=sync_run.documents_total or 0,
            documents_ok=sync_run.documents_ok or 0,
            documents_failed=sync_run.documents_failed or 0,
            bytes_processed=sync_run.bytes_processed or 0,
            error_details=sync_run.error_details,
            pages_done=pages_done,
            pages_total=pages_total,
            live_resolution_failed=live_resolution_failed,
        )
