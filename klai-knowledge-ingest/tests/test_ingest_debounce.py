"""
Tests for debounced KB ingest via Procrastinate queueing_lock + schedule_in.

The webhook handler must:
- Defer an ingest_from_gitea task with schedule_in and queueing_lock
- Silently skip (AlreadyEnqueued) when a pending task already exists
- Fall back to immediate ingest when enrichment is disabled
- Always process deletes immediately (no debounce)
"""
from __future__ import annotations

import sys
import types
import datetime
import json
import hashlib
import hmac
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal procrastinate stub (same pattern as test_ingest_enrichment_dedup)
# ---------------------------------------------------------------------------

class _AlreadyEnqueued(Exception):
    pass


def _install_procrastinate_stub():
    if "procrastinate" in sys.modules:
        return
    exceptions_mod = types.ModuleType("procrastinate.exceptions")
    exceptions_mod.AlreadyEnqueued = _AlreadyEnqueued  # type: ignore[attr-defined]
    pkg = types.ModuleType("procrastinate")
    pkg.exceptions = exceptions_mod  # type: ignore[attr-defined]
    sys.modules["procrastinate"] = pkg
    sys.modules["procrastinate.exceptions"] = exceptions_mod


_install_procrastinate_stub()
AlreadyEnqueued = sys.modules["procrastinate.exceptions"].AlreadyEnqueued


# ---------------------------------------------------------------------------
# Webhook payload helpers
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "test-secret"

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "commits": [{"added": ["docs/page.md"], "modified": [], "removed": []}],
    "repository": {"full_name": "org-myslug/my-kb"},
    "pusher": {"name": "testuser", "login": "testuser"},
}


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def webhook_client(mock_pool):
    """Test client with enrichment disabled at app startup (no Procrastinate init),
    but route-level settings mock reports enrichment_enabled=True so the debounce
    path is exercised without needing a real psycopg/libpq."""
    with patch("knowledge_ingest.config.settings.enrichment_enabled", False), \
         patch("knowledge_ingest.config.settings.gitea_webhook_secret", WEBHOOK_SECRET), \
         patch("knowledge_ingest.routes.ingest.settings") as mock_settings, \
         patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock), \
         patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock):

        mock_settings.gitea_webhook_secret = WEBHOOK_SECRET
        mock_settings.gitea_url = "http://gitea:3000"
        mock_settings.gitea_token = "token"
        mock_settings.enrichment_enabled = True   # route sees True → debounce path
        mock_settings.ingest_debounce_seconds = 180
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200

        import os

        from fastapi.testclient import TestClient
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            # SPEC-SEC-011: middleware now requires the header.
            c.headers.update(
                {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]}
            )
            yield c, mock_settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_webhook_defers_debounced_task(webhook_client):
    """Valid webhook should defer ingest_from_gitea with queueing_lock + schedule_in."""
    client, _ = webhook_client
    body = json.dumps(PUSH_PAYLOAD).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    mock_configured = MagicMock()
    mock_configured.defer_async = AsyncMock(return_value=None)
    mock_task = MagicMock()
    mock_task.configure = MagicMock(return_value=mock_configured)
    mock_proc_app = MagicMock()
    mock_proc_app.ingest_from_gitea = mock_task

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.enrichment_tasks.get_app",
        return_value=mock_proc_app,
    ):
        resp = client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={"content-type": "application/json", "x-gitea-signature": sig},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 1
    assert data["deleted"] == 0

    # configure() called with queueing_lock and schedule_in
    mock_task.configure.assert_called_once()
    call_kwargs = mock_task.configure.call_args.kwargs
    assert call_kwargs["queueing_lock"] == "gitea:zitadel-org-123:my-kb:docs/page.md"
    assert isinstance(call_kwargs["schedule_in"], datetime.timedelta)
    assert call_kwargs["schedule_in"].total_seconds() == 180

    # defer_async called with correct params
    mock_configured.defer_async.assert_called_once_with(
        org_id="zitadel-org-123",
        kb_slug="my-kb",
        path="docs/page.md",
        gitea_repo="org-myslug/my-kb",
        user_id=None,
    )


def test_webhook_already_enqueued_is_silent(webhook_client, caplog):
    """AlreadyEnqueued must be swallowed; response still 200 with queued=0."""
    client, _ = webhook_client
    body = json.dumps(PUSH_PAYLOAD).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    mock_configured = MagicMock()
    mock_configured.defer_async = AsyncMock(side_effect=AlreadyEnqueued())
    mock_task = MagicMock()
    mock_task.configure = MagicMock(return_value=mock_configured)
    mock_proc_app = MagicMock()
    mock_proc_app.ingest_from_gitea = mock_task

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.enrichment_tasks.get_app",
        return_value=mock_proc_app,
    ), caplog.at_level(logging.DEBUG, logger="knowledge_ingest.routes.ingest"):
        resp = client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={"content-type": "application/json", "x-gitea-signature": sig},
        )

    assert resp.status_code == 200
    assert resp.json()["queued"] == 0
    assert any("debounce active" in r.message for r in caplog.records)


def test_webhook_delete_is_immediate(webhook_client):
    """Deleted files must trigger immediate Qdrant + artifact removal, not be debounced."""
    client, _ = webhook_client

    delete_payload = {
        "ref": "refs/heads/main",
        "commits": [{"added": [], "modified": [], "removed": ["docs/old.md"]}],
        "repository": {"full_name": "org-myslug/my-kb"},
        "pusher": {"name": "testuser", "login": "testuser"},
    }
    body = json.dumps(delete_payload).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    mock_delete = AsyncMock(return_value=None)
    mock_soft_delete = AsyncMock(return_value=None)

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.qdrant_store.delete_document",
        mock_delete,
    ), patch(
        "knowledge_ingest.pg_store.soft_delete_artifact",
        mock_soft_delete,
    ):
        resp = client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={"content-type": "application/json", "x-gitea-signature": sig},
        )

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    mock_delete.assert_called_once_with("zitadel-org-123", "my-kb", "docs/old.md")
    mock_soft_delete.assert_called_once_with("zitadel-org-123", "my-kb", "docs/old.md")


@pytest.mark.asyncio
async def test_ingest_from_gitea_task_fetches_latest_content():
    """ingest_from_gitea must call _fetch_gitea_file at execution time, not use queued content."""
    mock_fetch = AsyncMock(return_value="# Latest content\nFresh text")
    mock_ingest = AsyncMock(return_value={"status": "ok", "chunks": 1})

    with patch(
        "knowledge_ingest.routes.ingest._fetch_gitea_file",
        mock_fetch,
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        mock_ingest,
    ):
        from knowledge_ingest.ingest_tasks import register_ingest_tasks

        # Build a minimal mock app and register the task
        registered_tasks: dict = {}

        class _MockApp:
            def task(self, queue=None, retry=None):
                def decorator(fn):
                    registered_tasks["ingest_from_gitea"] = fn
                    return fn
                return decorator

        app = _MockApp()
        register_ingest_tasks(app)

        await registered_tasks["ingest_from_gitea"](
            org_id="org1",
            kb_slug="kb",
            path="docs/page.md",
            gitea_repo="org-myslug/kb",
            user_id=None,
        )

    mock_fetch.assert_called_once_with("org-myslug/kb", "docs/page.md")
    mock_ingest.assert_called_once()
    req_arg = mock_ingest.call_args.args[0]
    assert req_arg.content == "# Latest content\nFresh text"
    assert req_arg.org_id == "org1"
