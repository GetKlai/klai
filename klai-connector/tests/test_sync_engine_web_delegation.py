"""Tests for SPEC-CRAWLER-004 Fase D — sync_engine delegates web_crawler syncs.

Covers AC-03.4 and AC-03.5:
- web_crawler syncs never touch the adapter registry when a CrawlSyncClient
  is wired in; instead /ingest/v1/crawl/sync is called exactly once and the
  returned job_id is stored on sync_run.cursor_state.
- Happy path: remote returns completed → sync_run status COMPLETED with
  pages_total/pages_done echoed from the remote response.
- Failure path: remote returns failed → sync_run status FAILED with
  error.details.service == "knowledge-ingest".
- Network/HTTP failure on enqueue → sync_run status FAILED, no retry.

All tests mock the CrawlSyncClient + session_maker so they never touch DB
or network. Poll intervals are shortened via SyncEngine class attributes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.enums import SyncStatus
from app.services.portal_client import PortalConnectorConfig
from app.services.sync_engine import SyncEngine


def _make_portal_config() -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id=str(uuid.uuid4()),
        kb_id=1,
        kb_slug="support",
        zitadel_org_id="368884765035593759",
        connector_type="web_crawler",
        config={
            "base_url": "https://help.voys.nl",
            "max_pages": 20,
            "path_prefix": None,
            "content_selector": "main",
            "canary_url": "https://help.voys.nl/index",
            "canary_fingerprint": "deadbeef12345678",
            "login_indicator_selector": "#login-form",
            "max_depth": 3,
        },
        schedule=None,
        is_enabled=True,
    )


def _make_sync_run_mock() -> MagicMock:
    sync_run = MagicMock()
    sync_run.status = None
    sync_run.completed_at = None
    sync_run.cursor_state = None
    sync_run.quality_status = None
    sync_run.documents_total = 0
    sync_run.documents_ok = 0
    sync_run.documents_failed = 0
    sync_run.error_details = None
    return sync_run


def _make_engine(
    *,
    crawl_sync_responses: list[dict] | None = None,
    status_responses: list[dict | Exception] | None = None,
    enqueue_exc: Exception | None = None,
) -> tuple[SyncEngine, MagicMock, MagicMock, MagicMock]:
    """Build a SyncEngine with mocked session_maker + CrawlSyncClient.

    Returns (engine, session_mock, sync_run_mock, portal_report_mock).
    """
    # SyncRun mock that the fake session hands back.
    sync_run = _make_sync_run_mock()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(return_value=sync_run)
    session.commit = AsyncMock()

    session_maker = MagicMock(return_value=session)

    # CrawlSyncClient mock. Defaults: one completed response.
    crawl_sync_client = MagicMock()
    if enqueue_exc is not None:
        crawl_sync_client.crawl_sync = AsyncMock(side_effect=enqueue_exc)
    else:
        crawl_sync_client.crawl_sync = AsyncMock(
            return_value=(crawl_sync_responses or [{"job_id": "job-123", "status": "queued"}])[0]
        )
    if status_responses is not None:
        crawl_sync_client.crawl_sync_status = AsyncMock(side_effect=status_responses)
    else:
        crawl_sync_client.crawl_sync_status = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "completed",
                "pages_total": 20,
                "pages_done": 20,
                "error": None,
            },
        )

    portal_client = MagicMock()
    portal_client.report_sync_status = AsyncMock()
    portal_client.get_connector_config = AsyncMock(return_value=_make_portal_config())

    ingest_client = MagicMock()
    registry = MagicMock()

    engine = SyncEngine(
        session_maker=session_maker,
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
        settings=MagicMock(),
        crawl_sync_client=crawl_sync_client,
    )
    # Shrink poll cadence so the test runs in ms, not seconds.
    engine._WEB_CRAWLER_POLL_INTERVAL_S = 0.001  # type: ignore[misc]
    engine._WEB_CRAWLER_POLL_TIMEOUT_S = 1.0  # type: ignore[misc]

    return engine, session, sync_run, portal_client.report_sync_status


class TestWebCrawlerDelegation:
    """Web-crawler syncs take the delegation path, not the adapter path."""

    @pytest.mark.asyncio
    async def test_happy_path_completes_sync_with_remote_counts(self) -> None:
        engine, session, sync_run, report_mock = _make_engine()
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        start_time = datetime.now(UTC).timestamp()

        # Bypass the locks + semaphore via the internal call.
        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=connector_id,
            sync_run_id=sync_run_id,
            start_time=start_time,
        )

        # POST /crawl/sync called exactly once with connector_id + config fields.
        engine._crawl_sync_client.crawl_sync.assert_awaited_once()
        call_kwargs = engine._crawl_sync_client.crawl_sync.await_args.kwargs
        assert call_kwargs["connector_id"] == str(connector_id)
        assert call_kwargs["org_id"] == "368884765035593759"
        assert call_kwargs["kb_slug"] == "support"
        assert call_kwargs["config"]["base_url"] == "https://help.voys.nl"

        # sync_run mutated with COMPLETED + counts + remote_job_id in cursor_state.
        assert sync_run.status == SyncStatus.COMPLETED
        assert sync_run.documents_total == 20
        assert sync_run.documents_ok == 20
        assert sync_run.documents_failed == 0
        assert sync_run.quality_status == "healthy"
        assert sync_run.cursor_state == {
            "remote_job_id": "job-123",
            "remote_status": "completed",
        }

        # portal report called with COMPLETED.
        report_mock.assert_awaited_once()
        assert report_mock.await_args.kwargs["sync_status"] == SyncStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_remote_failure_closes_sync_as_failed(self) -> None:
        engine, _, sync_run, _ = _make_engine(
            status_responses=[
                {
                    "job_id": "job-123",
                    "status": "failed",
                    "pages_total": 5,
                    "pages_done": 2,
                    "error": "auth_wall_detected: #login-form",
                },
            ],
        )
        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=uuid.uuid4(),
            sync_run_id=uuid.uuid4(),
            start_time=datetime.now(UTC).timestamp(),
        )

        assert sync_run.status == SyncStatus.FAILED
        assert sync_run.documents_total == 5
        assert sync_run.documents_ok == 2
        assert sync_run.documents_failed == 3
        assert sync_run.error_details and len(sync_run.error_details) == 1
        err = sync_run.error_details[0]
        assert err["service"] == "knowledge-ingest"
        assert "auth_wall_detected" in err["error"]
        assert err["remote_job_id"] == "job-123"

    @pytest.mark.asyncio
    async def test_running_status_keeps_polling_then_completes(self) -> None:
        """Intermediate 'pending'/'running' states are skipped until terminal."""
        engine, _, sync_run, _ = _make_engine(
            status_responses=[
                {"job_id": "job-123", "status": "pending", "pages_total": 0, "pages_done": 0, "error": None},
                {"job_id": "job-123", "status": "running", "pages_total": 20, "pages_done": 3, "error": None},
                {"job_id": "job-123", "status": "running", "pages_total": 20, "pages_done": 15, "error": None},
                {"job_id": "job-123", "status": "completed", "pages_total": 20, "pages_done": 20, "error": None},
            ],
        )
        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=uuid.uuid4(),
            sync_run_id=uuid.uuid4(),
            start_time=datetime.now(UTC).timestamp(),
        )
        assert sync_run.status == SyncStatus.COMPLETED
        assert sync_run.documents_ok == 20
        # Polled at least 4 times before terminal state.
        assert engine._crawl_sync_client.crawl_sync_status.await_count == 4

    @pytest.mark.asyncio
    async def test_enqueue_http_error_fails_sync_without_retry(self) -> None:
        """AC-03.5: non-2xx from POST /crawl/sync marks the sync as FAILED immediately."""
        fake_response = httpx.Response(
            503,
            request=httpx.Request("POST", "http://knowledge-ingest/ingest/v1/crawl/sync"),
            text="service unavailable",
        )
        engine, _, sync_run, _ = _make_engine(
            enqueue_exc=httpx.HTTPStatusError(
                "503 service unavailable", request=fake_response.request, response=fake_response,
            ),
        )
        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=uuid.uuid4(),
            sync_run_id=uuid.uuid4(),
            start_time=datetime.now(UTC).timestamp(),
        )

        assert sync_run.status == SyncStatus.FAILED
        assert sync_run.error_details
        assert sync_run.error_details[0]["service"] == "knowledge-ingest"
        assert "http_503" in sync_run.error_details[0]["error"]
        # Must NOT have polled — we never got a job_id.
        assert engine._crawl_sync_client.crawl_sync_status.await_count == 0

    @pytest.mark.asyncio
    async def test_enqueue_network_error_fails_sync(self) -> None:
        """A raw ConnectError during POST is surfaced as a failed sync_run."""
        engine, _, sync_run, _ = _make_engine(
            enqueue_exc=httpx.ConnectError("connection refused"),
        )
        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=uuid.uuid4(),
            sync_run_id=uuid.uuid4(),
            start_time=datetime.now(UTC).timestamp(),
        )
        assert sync_run.status == SyncStatus.FAILED
        assert sync_run.error_details[0]["service"] == "knowledge-ingest"
        assert "connection refused" in sync_run.error_details[0]["error"]

    @pytest.mark.asyncio
    async def test_poll_timeout_marks_sync_failed_but_preserves_job_id(self) -> None:
        """EC-1: the Procrastinate task may still complete later; keep job_id."""
        # Force a never-ending stream of non-terminal responses; timeout hits first.
        non_terminal = {
            "job_id": "job-123",
            "status": "running",
            "pages_total": 10,
            "pages_done": 1,
            "error": None,
        }
        engine, _, sync_run, _ = _make_engine(
            status_responses=[non_terminal] * 100,
        )
        # Tighten the timeout so the test finishes quickly.
        engine._WEB_CRAWLER_POLL_TIMEOUT_S = 0.01  # type: ignore[misc]
        engine._WEB_CRAWLER_POLL_INTERVAL_S = 0.001  # type: ignore[misc]

        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=uuid.uuid4(),
            sync_run_id=uuid.uuid4(),
            start_time=datetime.now(UTC).timestamp(),
        )

        assert sync_run.status == SyncStatus.FAILED
        assert sync_run.cursor_state["remote_job_id"] == "job-123"
        assert any(
            e.get("error") == "web_crawler_poll_timeout" for e in sync_run.error_details
        )


class TestCrawlSyncClientContract:
    """End-to-end check that CrawlSyncClient.crawl_sync forwards all fields."""

    @pytest.mark.asyncio
    async def test_crawl_sync_forwards_full_config(self) -> None:
        from app.clients.knowledge_ingest import CrawlSyncClient

        captured: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            import json

            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                202,
                json={"job_id": "new-job", "status": "queued"},
            )

        transport = httpx.MockTransport(_handler)
        client = CrawlSyncClient(base_url="http://knowledge-ingest:8000", internal_secret="s3cr3t")
        client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="http://knowledge-ingest:8000", transport=transport,
        )

        out = await client.crawl_sync(
            connector_id="abc",
            org_id="368884765035593759",
            kb_slug="support",
            config={
                "base_url": "https://help.voys.nl",
                "max_pages": 20,
                "max_depth": 2,
                "path_prefix": "/nl",
                "content_selector": "main",
                "canary_url": "https://help.voys.nl/index",
                "canary_fingerprint": "deadbeef12345678",
                "login_indicator_selector": "#login-form",
            },
        )

        assert out == {"job_id": "new-job", "status": "queued"}
        body = captured["body"]
        assert body["connector_id"] == "abc"
        assert body["org_id"] == "368884765035593759"
        assert body["kb_slug"] == "support"
        assert body["base_url"] == "https://help.voys.nl"
        assert body["max_pages"] == 20
        assert body["max_depth"] == 2
        assert body["path_prefix"] == "/nl"
        assert body["content_selector"] == "main"
        assert body["canary_url"] == "https://help.voys.nl/index"
        assert body["canary_fingerprint"] == "deadbeef12345678"
        assert body["login_indicator"] == "#login-form"
        assert captured["headers"]["x-internal-secret"] == "s3cr3t"

        await client.aclose()
