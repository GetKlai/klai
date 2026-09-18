"""Tests for POST /ingest/v1/crawl/keep-alive.

Background: a connector's stored session (browser cookies pasted once into
the wizard) goes idle-timeout between the once-daily scheduled crawl and its
previous use — proved in production on the Voys/support RedCactus connector,
where a manual re-sync 45 minutes after a fresh cookie paste succeeded 100%
but every unattended daily run since failed with auth_wall_detected on ~43%
of pages. This endpoint is klai-connector's periodic "touch": one cheap
authenticated GET so the session never goes idle. It is best-effort — it
must never surface a 500 to the caller.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from klai_image_storage.url_guard import ValidatedURL

from knowledge_ingest.connector_cookies import ConnectorNotFoundError
from tests.test_crawl_sync_endpoint import _client_with_patches, _make_pool

_VALIDATED = ValidatedURL(
    url="https://wiki.redcactus.cloud/",
    hostname="wiki.redcactus.cloud",
    pinned_ips=frozenset({"203.0.113.1"}),
    preferred_ip="203.0.113.1",
)


class _StatusOnly:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class TestCrawlKeepaliveEndpoint:
    def test_successful_touch_returns_ok_true(self) -> None:
        """Cookies decrypt and the pinned fetch returns 200 -> ok=True."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_StatusOnly(200),
            ),
        ):
            resp = client.post(
                "/ingest/v1/crawl/keep-alive",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "url": "https://wiki.redcactus.cloud/",
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_connector_not_found_returns_ok_false_no_500(self) -> None:
        """load_connector_cookies raising ConnectorNotFoundError -> ok=False, no 500."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                side_effect=ConnectorNotFoundError("connector not found"),
            ),
        ):
            resp = client.post(
                "/ingest/v1/crawl/keep-alive",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "url": "https://wiki.redcactus.cloud/",
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}

    def test_no_saved_credentials_returns_ok_false(self) -> None:
        """Empty cookie list (connector has no saved credentials) -> ok=False."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = client.post(
                "/ingest/v1/crawl/keep-alive",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "url": "https://wiki.redcactus.cloud/",
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}

    def test_login_redirect_returns_ok_false(self) -> None:
        """A 302 to a login page (expired session) must not report ok=True.

        _probe_fetch runs with follow_redirects=False, so an expired session
        redirecting to /login comes back as a bare 302 here -- exactly the
        RedCactus failure mode this endpoint exists to catch. A prior version
        of this handler used `status_code < 400`, which misclassified this as
        a live session.
        """
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_StatusOnly(302),
            ),
        ):
            resp = client.post(
                "/ingest/v1/crawl/keep-alive",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "url": "https://wiki.redcactus.cloud/",
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}

    def test_pinned_fetch_error_returns_ok_false_no_500(self) -> None:
        """The pinned fetch raising (e.g. connection error) -> ok=False, no 500."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=ConnectionError("connection refused"),
            ),
        ):
            resp = client.post(
                "/ingest/v1/crawl/keep-alive",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "url": "https://wiki.redcactus.cloud/",
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}
