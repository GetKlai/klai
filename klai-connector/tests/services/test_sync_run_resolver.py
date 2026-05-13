"""SPEC-CRAWLER-006: tests for the live-status resolver.

Covers REQ-04 (live resolution + terminalisation), REQ-05 (30s cache),
and REQ-08 backend portion (live progress fields on the rendered shape).

The resolver decides per-row whether live resolution applies. The marker
contract is ``status == RUNNING`` AND ``cursor_state.remote_job_id``
populated — set uniquely by SPEC-CRAWLER-006's fire-and-forget delegation.
Other connector types write terminal state inline and never leave a
remote_job_id on a RUNNING row, so they short-circuit through the
resolver without an upstream call.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.core.database as _db_module
from app.core.enums import SyncStatus
from app.services.sync_run_resolver import SyncRunResolver


@pytest.fixture(autouse=True)
def _reset_db_session_maker():
    """Save and restore app.core.database.session_maker around each test.

    _make_resolver() writes the mock session_maker into the module so that
    tenant_scoped_session() inside SyncRunResolver._finalize() works without
    a real database engine.  This fixture guarantees a clean state after
    each test regardless of whether the test passes or fails.
    """
    original = _db_module.session_maker
    yield
    _db_module.session_maker = original


def _row(
    *,
    status: str = SyncStatus.RUNNING,
    remote_job_id: str | None = "abc123",
    documents_total: int = 0,
    documents_ok: int = 0,
    documents_failed: int = 0,
    completed_at: datetime | None = None,
    error_details: list[dict] | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.connector_id = uuid.uuid4()
    row.status = status
    row.started_at = datetime.now(UTC)
    row.completed_at = completed_at
    row.documents_total = documents_total
    row.documents_ok = documents_ok
    row.documents_failed = documents_failed
    row.bytes_processed = 0
    row.error_details = error_details
    row.cursor_state = {"remote_job_id": remote_job_id, "remote_status": "queued"} if remote_job_id else None
    return row


def _make_resolver(
    *,
    status_responses: list[dict | Exception] | None = None,
) -> tuple[SyncRunResolver, MagicMock, MagicMock, MagicMock]:
    """Resolver wired to mocks. Returns (resolver, crawl_client, session, portal)."""
    crawl_client = MagicMock()
    if status_responses is not None:
        crawl_client.crawl_sync_status = AsyncMock(side_effect=status_responses)
    else:
        crawl_client.crawl_sync_status = AsyncMock(
            return_value={"job_id": "abc123", "status": "running", "pages_done": 5, "pages_total": 100, "error": None},
        )

    finalized: dict = {}

    def _make_session_mock() -> MagicMock:
        # The resolver calls
        # session.get(SyncRun, id, with_for_update=True) inside _finalize.
        # We use a closure over the row passed to .resolve() so the
        # finalize path mutates the same MagicMock the test inspects.
        # **kwargs swallows ``with_for_update=True`` (and any future
        # session.get options) so the lambda accepts the resolver's
        # actual signature.
        sess = MagicMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.get = AsyncMock(side_effect=lambda model, row_id, **kwargs: finalized.get(row_id))
        sess.commit = AsyncMock()
        sess.refresh = AsyncMock()
        # SPEC-TI-002: _pin_and_reset_connection calls await session.connection()
        # and await session.rollback() inside tenant_scoped_session.
        sess.connection = AsyncMock()
        sess.rollback = AsyncMock()
        # execute is called by set_config GUC statements inside the session helpers.
        sess.execute = AsyncMock()
        return sess

    session = _make_session_mock()
    session_maker = MagicMock(return_value=session)

    # SPEC-TI-002: tenant_scoped_session() is a module-level helper that
    # checks app.core.database.session_maker.  Inject the mock so _finalize()
    # works without a real database engine.
    _db_module.session_maker = session_maker

    portal = MagicMock()
    portal.report_sync_status = AsyncMock()

    resolver = SyncRunResolver(
        crawl_sync_client=crawl_client,
        session_maker=session_maker,
        portal_client=portal,
    )
    # Expose the finalize-row-registry to tests so they can stage rows.
    resolver._test_finalize_registry = finalized  # type: ignore[attr-defined]
    return resolver, crawl_client, session, portal


def _register(resolver: SyncRunResolver, row: MagicMock) -> None:
    """Make ``row`` discoverable by ``session.get`` inside _finalize."""
    resolver._test_finalize_registry[row.id] = row  # type: ignore[attr-defined]


class TestResolveDispatch:
    """REQ-04.1: only RUNNING rows with a remote_job_id are resolved live."""

    @pytest.mark.asyncio
    async def test_terminal_row_does_not_call_upstream(self) -> None:
        resolver, crawl_client, _, portal = _make_resolver()
        row = _row(status=SyncStatus.COMPLETED, documents_total=20, documents_ok=20, completed_at=datetime.now(UTC))
        snap = await resolver.resolve(row)
        crawl_client.crawl_sync_status.assert_not_awaited()
        portal.report_sync_status.assert_not_awaited()
        assert snap.status == SyncStatus.COMPLETED
        assert snap.pages_done is None
        assert snap.pages_total is None
        assert snap.live_resolution_failed is False

    @pytest.mark.asyncio
    async def test_running_without_remote_job_id_does_not_call_upstream(self) -> None:
        """Notion / GitHub / etc. — RUNNING but no delegation marker."""
        resolver, crawl_client, _, _ = _make_resolver()
        row = _row(remote_job_id=None)
        snap = await resolver.resolve(row)
        crawl_client.crawl_sync_status.assert_not_awaited()
        assert snap.status == SyncStatus.RUNNING
        assert snap.pages_done is None
        assert snap.pages_total is None


class TestResolveRunningCrawler:
    """AC-04.1: RUNNING crawler -> live progress, no DB write."""

    @pytest.mark.asyncio
    async def test_running_returns_live_pages_done(self) -> None:
        resolver, crawl_client, session, portal = _make_resolver(
            status_responses=[
                {"job_id": "abc123", "status": "running", "pages_done": 42, "pages_total": 500, "error": None},
            ],
        )
        row = _row()
        snap = await resolver.resolve(row)

        crawl_client.crawl_sync_status.assert_awaited_once_with("abc123")
        assert snap.status == SyncStatus.RUNNING
        assert snap.pages_done == 42
        assert snap.pages_total == 500
        # No commit, no portal report on intermediate state.
        session.commit.assert_not_awaited()
        portal.report_sync_status.assert_not_awaited()


class TestResolveTerminalCompleted:
    """AC-04.2: completed remote -> finalize local row + portal callback."""

    @pytest.mark.asyncio
    async def test_completed_writes_terminal_state_and_reports(self) -> None:
        resolver, crawl_client, session, portal = _make_resolver(
            status_responses=[
                {"job_id": "abc123", "status": "completed", "pages_done": 368, "pages_total": 368, "error": None},
            ],
        )
        row = _row()
        _register(resolver, row)

        snap = await resolver.resolve(row)

        assert row.status == SyncStatus.COMPLETED
        assert row.documents_ok == 368
        assert row.documents_total == 368
        assert row.documents_failed == 0
        assert row.error_details is None
        assert row.quality_status == "healthy"
        assert row.cursor_state["remote_status"] == "completed"

        session.commit.assert_awaited_once()
        portal.report_sync_status.assert_awaited_once()
        assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.COMPLETED
        assert portal.report_sync_status.await_args.kwargs["documents_ok"] == 368

        assert snap.status == SyncStatus.COMPLETED
        assert snap.pages_done == 368
        assert snap.pages_total == 368


class TestResolveTerminalFailed:
    """AC-04.3: failed remote -> finalize as FAILED with error_details."""

    @pytest.mark.asyncio
    async def test_failed_writes_terminal_state_with_error(self) -> None:
        resolver, crawl_client, session, portal = _make_resolver(
            status_responses=[
                {
                    "job_id": "abc123",
                    "status": "failed",
                    "pages_done": 17,
                    "pages_total": 500,
                    "error": "timeout_per_page",
                },
            ],
        )
        row = _row()
        _register(resolver, row)

        snap = await resolver.resolve(row)

        assert row.status == SyncStatus.FAILED
        assert row.documents_ok == 17
        assert row.documents_failed == 483
        assert row.error_details
        assert row.error_details[0]["error"] == "timeout_per_page"
        assert row.error_details[0]["service"] == "knowledge-ingest"
        assert row.quality_status is None

        portal.report_sync_status.assert_awaited_once()
        assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.FAILED

        assert snap.status == SyncStatus.FAILED


class TestResolveUpstreamUnreachable:
    """AC-04.4: knowledge-ingest down -> degraded snapshot, no DB write."""

    @pytest.mark.asyncio
    async def test_connect_error_returns_running_with_flag(self) -> None:
        resolver, _, session, portal = _make_resolver(
            status_responses=[httpx.ConnectError("connection refused")],
        )
        row = _row()
        snap = await resolver.resolve(row)

        assert snap.status == SyncStatus.RUNNING
        assert snap.live_resolution_failed is True
        assert snap.pages_done is None
        assert snap.pages_total is None
        session.commit.assert_not_awaited()
        portal.report_sync_status.assert_not_awaited()


class TestResolveCache:
    """REQ-CRAWLER-006-05: 30s TTL per remote_job_id."""

    @pytest.mark.asyncio
    async def test_repeated_calls_within_ttl_hit_cache(self) -> None:
        resolver, crawl_client, _, _ = _make_resolver(
            status_responses=[
                {"job_id": "abc123", "status": "running", "pages_done": 1, "pages_total": 100, "error": None},
            ]
            * 3,
        )
        row = _row()
        await resolver.resolve(row)
        await resolver.resolve(row)
        await resolver.resolve(row)
        # AC-05.1: exactly one upstream call across three reads.
        assert crawl_client.crawl_sync_status.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self) -> None:
        resolver, crawl_client, _, _ = _make_resolver(
            status_responses=[
                {"job_id": "abc123", "status": "running", "pages_done": 1, "pages_total": 100, "error": None},
                {"job_id": "abc123", "status": "running", "pages_done": 5, "pages_total": 100, "error": None},
            ],
        )
        row = _row()
        await resolver.resolve(row)
        # Force the cache entry past the 30 s TTL.
        entry = resolver._cache["abc123"]
        entry.fetched_at = time.monotonic() - 31.0
        await resolver.resolve(row)
        # AC-05.2: two upstream calls.
        assert crawl_client.crawl_sync_status.await_count == 2

    @pytest.mark.asyncio
    async def test_cache_keys_are_per_job_id(self) -> None:
        """Two distinct job_ids do not share a cache slot."""
        resolver, crawl_client, _, _ = _make_resolver(
            status_responses=[
                {"job_id": "job-a", "status": "running", "pages_done": 1, "pages_total": 10, "error": None},
                {"job_id": "job-b", "status": "running", "pages_done": 2, "pages_total": 20, "error": None},
            ],
        )
        await resolver.resolve(_row(remote_job_id="job-a"))
        await resolver.resolve(_row(remote_job_id="job-b"))
        assert crawl_client.crawl_sync_status.await_count == 2

    @pytest.mark.asyncio
    async def test_cache_gc_drops_stale_entries(self) -> None:
        """A new write evicts cache entries older than the GC window so
        the cache cannot grow unbounded across the process lifetime."""
        from app.services.sync_run_resolver import _CACHE_GC_S

        resolver, _, _, _ = _make_resolver(
            status_responses=[
                {"job_id": "old-job", "status": "running", "pages_done": 1, "pages_total": 10, "error": None},
                {"job_id": "new-job", "status": "running", "pages_done": 2, "pages_total": 20, "error": None},
            ],
        )
        await resolver.resolve(_row(remote_job_id="old-job"))
        # Backdate the existing cache entry past the GC window.
        old_entry = resolver._cache["old-job"]
        old_entry.fetched_at = time.monotonic() - _CACHE_GC_S - 1.0
        # A miss for "new-job" triggers the GC sweep before insertion.
        await resolver.resolve(_row(remote_job_id="new-job"))
        assert "old-job" not in resolver._cache
        assert "new-job" in resolver._cache


class TestResolveIdempotence:
    """The resolver must not double-write when two readers race."""

    @pytest.mark.asyncio
    async def test_second_resolver_call_after_terminalization_is_noop(self) -> None:
        """A row already in COMPLETED state on a second read leaves no
        side-effects beyond the cached upstream payload."""
        resolver, crawl_client, session, portal = _make_resolver(
            status_responses=[
                {"job_id": "abc123", "status": "completed", "pages_done": 10, "pages_total": 10, "error": None},
            ]
            * 5,
        )
        row = _row()
        _register(resolver, row)
        await resolver.resolve(row)

        # Simulate a second reader: the row in DB is now COMPLETED. The
        # resolver short-circuits at the dispatch step (status check).
        commits_after_first = session.commit.await_count
        portal_calls_after_first = portal.report_sync_status.await_count

        # Second .resolve() with the now-terminal row.
        row.status = SyncStatus.COMPLETED  # mirror what _finalize did
        snap = await resolver.resolve(row)

        # No additional commits or portal callbacks.
        assert session.commit.await_count == commits_after_first
        assert portal.report_sync_status.await_count == portal_calls_after_first
        assert snap.status == SyncStatus.COMPLETED
