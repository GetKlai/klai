"""SPEC-CRAWLER-006: tests for fire-and-forget web_crawler delegation.

Replaces the SPEC-CRAWLER-004 Fase D synchronous-poll tests. Under the
new contract, ``_run_web_crawler_delegation`` returns immediately after
enqueue with ``sync_run.status = RUNNING`` and the ``remote_job_id``
stored on ``cursor_state``. Live progress and terminal state happen at
read time via :class:`SyncRunResolver` (covered separately in
``tests/services/test_sync_run_resolver.py``).

Synchronous failure paths (SSRF, HTTP error, network error) still close
the run as ``FAILED`` before any RUNNING state is observable.
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
    sync_run.status = SyncStatus.RUNNING
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
    enqueue_response: dict | None = None,
    enqueue_exc: Exception | None = None,
) -> tuple[SyncEngine, MagicMock, MagicMock, MagicMock]:
    """Build a SyncEngine with mocked session_maker + CrawlSyncClient.

    Returns (engine, session_mock, sync_run_mock, portal_report_mock).
    """
    sync_run = _make_sync_run_mock()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(return_value=sync_run)
    session.commit = AsyncMock()

    session_maker = MagicMock(return_value=session)

    crawl_sync_client = MagicMock()
    if enqueue_exc is not None:
        crawl_sync_client.crawl_sync = AsyncMock(side_effect=enqueue_exc)
    else:
        crawl_sync_client.crawl_sync = AsyncMock(
            return_value=enqueue_response or {"job_id": "job-123", "status": "queued"},
        )
    # SPEC-CRAWLER-006 REQ-02 / REQ-03: status + cancel MUST NOT be called
    # from sync_engine. Configuring AsyncMocks here lets the assertions
    # below verify they remain untouched.
    crawl_sync_client.crawl_sync_status = AsyncMock()
    crawl_sync_client.crawl_sync_cancel = AsyncMock()

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
    return engine, session, sync_run, portal_client.report_sync_status


class TestFireAndForgetDelegation:
    """REQ-CRAWLER-006-01..03: enqueue, store remote_job_id, return."""

    @pytest.mark.asyncio
    async def test_successful_enqueue_leaves_run_in_running_state(self) -> None:
        engine, session, sync_run, report_mock = _make_engine(
            enqueue_response={"job_id": "abc123", "status": "queued"},
        )
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()

        await engine._run_web_crawler_delegation(
            portal_config=_make_portal_config(),
            connector_id=connector_id,
            sync_run_id=sync_run_id,
            start_time=datetime.now(UTC).timestamp(),
        )

        # AC-01.1: enqueue happened with the right fields.
        engine._crawl_sync_client.crawl_sync.assert_awaited_once()
        call_kwargs = engine._crawl_sync_client.crawl_sync.await_args.kwargs
        assert call_kwargs["connector_id"] == str(connector_id)
        assert call_kwargs["org_id"] == "368884765035593759"
        assert call_kwargs["kb_slug"] == "support"

        # AC-01.1: cursor_state stores remote_job_id; status stays RUNNING.
        assert sync_run.cursor_state == {
            "remote_job_id": "abc123",
            "remote_status": "queued",
        }
        assert sync_run.status == SyncStatus.RUNNING
        assert sync_run.completed_at is None

        # AC-02.1 / AC-02.2: NO polling, NO cancel.
        engine._crawl_sync_client.crawl_sync_status.assert_not_awaited()
        engine._crawl_sync_client.crawl_sync_cancel.assert_not_awaited()

        # No portal callback on the success path — terminal state is
        # reported by SyncRunResolver after the remote job finishes.
        report_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enqueue_http_error_fails_sync_synchronously(self) -> None:
        """AC-01.2: non-2xx from /crawl/sync -> FAILED, http_<code> error."""
        fake_response = httpx.Response(
            503,
            request=httpx.Request("POST", "http://knowledge-ingest/ingest/v1/crawl/sync"),
            text="service unavailable",
        )
        engine, _, sync_run, report_mock = _make_engine(
            enqueue_exc=httpx.HTTPStatusError(
                "503 service unavailable",
                request=fake_response.request,
                response=fake_response,
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
        err = sync_run.error_details[0]
        assert err["service"] == "knowledge-ingest"
        assert err["error"] == "http_503"

        # Synchronous failure DOES emit the portal callback.
        report_mock.assert_awaited_once()
        assert report_mock.await_args.kwargs["sync_status"] == SyncStatus.FAILED

    @pytest.mark.asyncio
    async def test_enqueue_network_error_fails_sync_synchronously(self) -> None:
        engine, _, sync_run, report_mock = _make_engine(
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
        report_mock.assert_awaited_once()


class TestCrawlSyncClientContract:
    """End-to-end check that CrawlSyncClient.crawl_sync forwards all fields.

    Independent of SPEC-CRAWLER-006 behaviour — kept from the
    SPEC-CRAWLER-004 test suite because the wire contract did not change.
    """

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
