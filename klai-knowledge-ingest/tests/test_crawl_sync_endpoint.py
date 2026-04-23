"""Tests for POST /ingest/v1/crawl/sync and /status (SPEC-CRAWLER-004 Fase C).

Covers:
- REQ-03.1 endpoint accepts full config + is protected by X-Internal-Secret
- AC-03.1 happy path returns 202 with job_id + status=queued, creates
  crawl_jobs row, queues Procrastinate task
- AC-03.2 missing X-Internal-Secret is rejected (401)
- AC-03.3 unknown connector_id → 404
- REQ-01.3 cookies are decrypted in-process via the shared lib; plaintext
  cookies travel only in the in-process Procrastinate defer_async call,
  never over the wire.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from connector_credentials import AESGCMCipher, ConnectorCredentialStore
from fastapi.testclient import TestClient

# conftest.py already seeded KNOWLEDGE_INGEST_SECRET + PORTAL_INTERNAL_TOKEN;
# the endpoint additionally needs ENCRYPTION_KEY. We patch ``settings.encryption_key``
# directly in each test so module-load ordering between test files cannot
# bypass the env-var.
_TEST_KEK_HEX: str = os.urandom(32).hex()


def _encrypted_cookies_fixture(kek_hex: str) -> tuple[bytes, bytes, list[dict]]:
    """Return (encrypted_credentials, connector_dek_enc, plaintext_cookies)."""
    cookies = [{"name": "sid", "value": "abc123"}]
    raw_dek = os.urandom(32)

    kek_cipher = AESGCMCipher(bytes.fromhex(kek_hex))
    connector_dek_enc = kek_cipher.encrypt(raw_dek.hex())

    dek_cipher = AESGCMCipher(raw_dek)
    encrypted_credentials = dek_cipher.encrypt(json.dumps({"cookies": cookies}))
    return encrypted_credentials, connector_dek_enc, cookies


def _make_pool(
    *,
    connector_row: dict | None = None,
    job_row: dict | None = None,
) -> MagicMock:
    """Return a mock asyncpg pool whose fetchrow dispatches by SQL prefix."""
    pool = MagicMock()

    async def _fetchrow(stmt: str, *args: object, **_kwargs: object) -> dict | None:
        if "portal_connectors" in stmt:
            return connector_row
        if "knowledge.crawl_jobs" in stmt and "SELECT" in stmt:
            if job_row is None:
                return None
            # Echo the id the handler queried with so the response matches.
            return {**job_row, "id": args[0]}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(return_value=None)
    return pool


@contextmanager
def _client_with_patches(pool: MagicMock):
    """Yield (TestClient, defer_async_mock) with DB + Procrastinate patched.

    Keeping the patches active for the entire request lifecycle (not just
    startup) is critical — the endpoint calls ``enrichment_tasks.get_app()``
    at request time.
    """
    defer_async = AsyncMock(return_value=None)
    mock_task = MagicMock()
    mock_task.defer_async = defer_async
    mock_proc_app = MagicMock()
    mock_proc_app.run_crawl = mock_task

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        # The route file did ``from knowledge_ingest.db import get_pool`` at
        # module load, so the patch above on ``db.get_pool`` does not reach
        # the already-bound name inside the route. Patch the route-local
        # reference explicitly.
        patch(
            "knowledge_ingest.routes.crawl_sync.get_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
        patch(
            "knowledge_ingest.config.settings.encryption_key",
            _TEST_KEK_HEX,
        ),
        patch(
            "knowledge_ingest.enrichment_tasks.get_app",
            return_value=mock_proc_app,
        ),
    ):
        from knowledge_ingest.app import app

        with TestClient(app) as client:
            client.headers.update(
                {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]},
            )
            yield client, defer_async


@pytest.fixture()
def kek_hex() -> str:
    return _TEST_KEK_HEX


@pytest.fixture()
def payload_with_cookies(kek_hex: str):
    return _encrypted_cookies_fixture(kek_hex)


class TestCrawlSyncEndpoint:
    """POST /ingest/v1/crawl/sync — happy path + auth + unknown connector."""

    def test_missing_x_internal_secret_is_401(self) -> None:
        """AC-03.2: the middleware rejects unauthenticated callers."""
        pool = _make_pool()
        with _client_with_patches(pool) as (client, _defer):
            client.headers.clear()
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://help.voys.nl",
                },
            )
        assert resp.status_code == 401

    def test_unknown_connector_returns_404(self) -> None:
        """AC-03.3: missing portal_connectors row returns 404."""
        pool = _make_pool(connector_row=None)
        with _client_with_patches(pool) as (client, _defer):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://help.voys.nl",
                },
            )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "connector_not_found"

    def test_org_mismatch_returns_409(self, payload_with_cookies) -> None:
        """connector belongs to a different org than the request body."""
        encrypted, dek_enc, _ = payload_with_cookies
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=1),
                "zitadel_org_id": "999999",
                "encrypted_credentials": encrypted,
                "connector_dek_enc": dek_enc,
            },
        )
        with _client_with_patches(pool) as (client, _defer):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://help.voys.nl",
                },
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "connector_org_mismatch"

    def test_happy_path_enqueues_task_with_connector_id(
        self,
        payload_with_cookies,
    ) -> None:
        """AC-03.1: happy path — 202, connector_id passed to task, NO cookies in args.

        REQ-05.4: plaintext cookies must not enter the Procrastinate args log;
        the task reloads them at execution time.
        """
        encrypted, dek_enc, expected_cookies = payload_with_cookies
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=1),
                "zitadel_org_id": "42",
                "encrypted_credentials": encrypted,
                "connector_dek_enc": dek_enc,
            },
        )
        sent_connector_id = str(uuid.uuid4())
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": sent_connector_id,
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://help.voys.nl",
                    "max_pages": 20,
                    "login_indicator": "#login-form",
                    "canary_url": "https://help.voys.nl/index",
                    "canary_fingerprint": "deadbeef12345678",
                },
            )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) > 0

        # crawl_jobs INSERT must have been issued.
        insert_calls = [
            c
            for c in pool.execute.await_args_list
            if c.args and "INSERT INTO knowledge.crawl_jobs" in c.args[0]
        ]
        assert insert_calls, "expected INSERT into knowledge.crawl_jobs"
        # Never persist plaintext cookies into the audit row.
        config_json = insert_calls[0].args[4]
        for plaintext in expected_cookies:
            assert plaintext["value"] not in config_json
        assert "cookies" not in config_json

        # Procrastinate defer kwargs: connector_id present, cookies ABSENT.
        defer_mock.assert_awaited_once()
        kwargs = defer_mock.await_args.kwargs
        assert kwargs["connector_id"] == sent_connector_id
        assert "cookies" not in kwargs
        for plaintext in expected_cookies:
            # Strong guarantee: the raw cookie value never appears in any kwarg.
            for value in kwargs.values():
                assert plaintext["value"] not in str(value)
        assert kwargs["login_indicator_selector"] == "#login-form"
        assert kwargs["canary_url"] == "https://help.voys.nl/index"
        assert kwargs["canary_fingerprint"] == "deadbeef12345678"
        assert kwargs["start_url"] == "https://help.voys.nl"
        assert kwargs["max_pages"] == 20
        assert kwargs["org_id"] == "42"
        assert kwargs["kb_slug"] == "support"

    def test_path_prefix_is_prepended_to_start_url(self) -> None:
        """BFS must enter the allowed subtree; start_url = base_url + path_prefix.

        Wiki.redcactus.cloud homepage only links to /en/... so starting BFS on the
        bare root with include_patterns=['/nl/'] stops after 1 page (filter rejects
        every outgoing link). Starting on /nl/ gives BFS a seeded entry.
        """
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=2),
                "zitadel_org_id": "42",
                "encrypted_credentials": None,
                "connector_dek_enc": None,
            },
        )
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://wiki.redcactus.cloud",
                    "path_prefix": "/nl/",
                },
            )
        assert resp.status_code == 202, resp.text
        kwargs = defer_mock.await_args.kwargs
        assert kwargs["start_url"] == "https://wiki.redcactus.cloud/nl/"
        # URLPatternFilter exact-matches glob patterns without wildcards, so
        # '/nl/' alone would reject '/nl/6-bubble'. The /* suffix makes it a
        # PREFIX pattern so every URL whose path starts with /nl/ is allowed.
        assert kwargs["include_patterns"] == ["/nl/*"]

    def test_trailing_slash_in_base_url_is_normalised(self) -> None:
        """Avoid building 'https://host//nl/' when base_url already ends with '/'."""
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=3),
                "zitadel_org_id": "42",
                "encrypted_credentials": None,
                "connector_dek_enc": None,
            },
        )
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://wiki.redcactus.cloud/",
                    "path_prefix": "/nl/",
                },
            )
        assert resp.status_code == 202, resp.text
        kwargs = defer_mock.await_args.kwargs
        assert kwargs["start_url"] == "https://wiki.redcactus.cloud/nl/"

    def test_path_prefix_without_trailing_slash_is_normalised(self) -> None:
        """path_prefix='/nl' (no trailing /) still yields a valid PREFIX glob."""
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=4),
                "zitadel_org_id": "42",
                "encrypted_credentials": None,
                "connector_dek_enc": None,
            },
        )
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://wiki.redcactus.cloud",
                    "path_prefix": "/nl",
                },
            )
        assert resp.status_code == 202, resp.text
        kwargs = defer_mock.await_args.kwargs
        # Glob pattern gets the /* suffix after stripping a (non-existent)
        # trailing slash — same result as when the slash WAS present.
        assert kwargs["include_patterns"] == ["/nl/*"]
        # start_url still appends the literal path_prefix, so absence of
        # trailing slash here means BFS starts on /nl (which the server
        # typically redirects to /nl/). That is acceptable behaviour for a
        # user who entered /nl without slash.
        assert kwargs["start_url"] == "https://wiki.redcactus.cloud/nl"

    def test_path_prefix_with_nested_base_url(self) -> None:
        """base_url with its own path + path_prefix stacks paths cleanly."""
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=5),
                "zitadel_org_id": "42",
                "encrypted_credentials": None,
                "connector_dek_enc": None,
            },
        )
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://example.com/wiki/",
                    "path_prefix": "/nl/",
                },
            )
        assert resp.status_code == 202, resp.text
        kwargs = defer_mock.await_args.kwargs
        # Double slash collapses because base_url is rstripped before concat.
        assert kwargs["start_url"] == "https://example.com/wiki/nl/"
        assert kwargs["include_patterns"] == ["/nl/*"]

    def test_public_crawl_still_enqueues(self) -> None:
        """Connector with no encrypted credentials still enqueues; task gets no cookies."""
        pool = _make_pool(
            connector_row={
                "id": uuid.UUID(int=1),
                "zitadel_org_id": "42",
                "encrypted_credentials": None,
                "connector_dek_enc": None,
            },
        )
        with _client_with_patches(pool) as (client, defer_mock):
            resp = client.post(
                "/ingest/v1/crawl/sync",
                json={
                    "connector_id": str(uuid.uuid4()),
                    "org_id": "42",
                    "kb_slug": "support",
                    "base_url": "https://public.example",
                },
            )
        assert resp.status_code == 202, resp.text
        kwargs = defer_mock.await_args.kwargs
        assert "cookies" not in kwargs
        assert "connector_id" in kwargs


class TestCrawlSyncStatusEndpoint:
    """GET /ingest/v1/crawl/sync/{job_id}/status — polling surface."""

    def test_returns_status_for_known_job(self) -> None:
        pool = _make_pool(
            job_row={
                "status": "running",
                "pages_total": 20,
                "pages_done": 7,
                "error": None,
            },
        )
        job_id = str(uuid.uuid4())
        with _client_with_patches(pool) as (client, _defer):
            resp = client.get(f"/ingest/v1/crawl/sync/{job_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["status"] == "running"
        assert body["pages_total"] == 20
        assert body["pages_done"] == 7
        assert body["error"] is None

    def test_unknown_job_returns_404(self) -> None:
        pool = _make_pool(job_row=None)
        with _client_with_patches(pool) as (client, _defer):
            resp = client.get(f"/ingest/v1/crawl/sync/{uuid.uuid4()}/status")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "job_not_found"


class TestDecryptFromBlobs:
    """End-to-end guarantee for the shared lib's blob decrypt method."""

    def test_blob_round_trip(self, payload_with_cookies, kek_hex: str) -> None:
        encrypted, dek_enc, expected_cookies = payload_with_cookies
        store = ConnectorCredentialStore(kek_hex)
        out = store.decrypt_credentials_from_blobs(
            encrypted_credentials=encrypted,
            connector_dek_enc=dek_enc,
        )
        assert out["cookies"] == expected_cookies
