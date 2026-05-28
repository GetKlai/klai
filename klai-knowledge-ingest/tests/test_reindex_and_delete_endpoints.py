"""Tests for SPEC-PORTAL-KENNIS-002 Track 3 endpoints:
POST /knowledge/v1/artifacts/{artifact_id}/reindex
DELETE /knowledge/v1/kb/{kb_slug}/uploads/{artifact_id}
"""

import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Procrastinate stub (guard: may already be installed by conftest)
# ---------------------------------------------------------------------------
def _install_procrastinate_stub() -> None:
    if "procrastinate" in sys.modules:
        return

    import types

    proc_mod = types.ModuleType("procrastinate")
    proc_mod.App = MagicMock  # type: ignore[attr-defined]
    proc_mod.SyncPsycopgConnector = MagicMock  # type: ignore[attr-defined]
    sys.modules["procrastinate"] = proc_mod

    contrib_mod = types.ModuleType("procrastinate.contrib")
    sys.modules["procrastinate.contrib"] = contrib_mod
    aio_mod = types.ModuleType("procrastinate.contrib.aiopg")
    aio_mod.AiopgConnector = MagicMock  # type: ignore[attr-defined]
    sys.modules["procrastinate.contrib.aiopg"] = aio_mod


_install_procrastinate_stub()

import os  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

_INTERNAL_HEADER = {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]}
_ORG = "org-1"
_ARTIFACT = "aaaabbbb-0000-0000-0000-000000000001"
_KB = "my-kb"
_PATH = "uploads/file.pdf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_conn() -> MagicMock:
    """Minimal asyncpg connection mock (mirrors conftest._make_mock_conn)."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    # Transaction context manager
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


@contextmanager
def _client_with_conn(conn: MagicMock):  # type: ignore[return]
    """Build a TestClient that yields *conn* from tenant_scoped_connection."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_tenant(org_id: str):
        yield conn

    mock_pool = MagicMock()

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
        patch("knowledge_ingest.routes.kb_sources.tenant_scoped_connection", _fake_tenant),
    ):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers.update(_INTERNAL_HEADER)
            yield c


# ---------------------------------------------------------------------------
# POST reindex — happy path
# ---------------------------------------------------------------------------
class TestReindexHappyPath:
    def test_returns_202_with_pending_status(self) -> None:
        conn = _make_mock_conn()
        # set_artifact_index_status returns the updated row
        conn.fetchrow = AsyncMock(return_value={"artifact_id": _ARTIFACT, "path": _PATH})

        mock_job = MagicMock()
        mock_job.configure = MagicMock(return_value=mock_job)
        mock_job.defer_async = AsyncMock(return_value=None)

        mock_proc_app = MagicMock()
        mock_proc_app.enrich_document_interactive = mock_job

        with (
            patch(
                "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
                AsyncMock(return_value=_ORG),
            ),
            patch(
                "knowledge_ingest.enrichment_tasks.get_app",
                return_value=mock_proc_app,
            ),
        ):
            with _client_with_conn(conn) as client:
                resp = client.post(
                    f"/knowledge/v1/artifacts/{_ARTIFACT}/reindex",
                    params={"org_id": _ORG},
                )

        assert resp.status_code == 202
        body = resp.json()
        assert body["artifact_id"] == _ARTIFACT
        assert body["index_status"] == "pending"

    def test_sets_index_status_to_pending_in_db(self) -> None:
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"artifact_id": _ARTIFACT, "path": _PATH})

        mock_job = MagicMock()
        mock_job.configure = MagicMock(return_value=mock_job)
        mock_job.defer_async = AsyncMock(return_value=None)
        mock_proc_app = MagicMock()
        mock_proc_app.enrich_document_interactive = mock_job

        with (
            patch(
                "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
                AsyncMock(return_value=_ORG),
            ),
            patch(
                "knowledge_ingest.enrichment_tasks.get_app",
                return_value=mock_proc_app,
            ),
        ):
            with _client_with_conn(conn) as client:
                client.post(
                    f"/knowledge/v1/artifacts/{_ARTIFACT}/reindex",
                    params={"org_id": _ORG},
                )

        # The UPDATE call should pass "pending" as the first positional arg
        sql = conn.fetchrow.await_args.args[0]
        assert "UPDATE knowledge.artifacts" in sql
        assert "index_status" in sql
        status_arg = conn.fetchrow.await_args.args[1]
        assert status_arg == "pending"


# ---------------------------------------------------------------------------
# POST reindex — 404
# ---------------------------------------------------------------------------
class TestReindexNotFound:
    def test_returns_404_when_artifact_missing(self) -> None:
        conn = _make_mock_conn()
        # set_artifact_index_status returns None → artifact not found
        conn.fetchrow = AsyncMock(return_value=None)

        with patch(
            "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
            AsyncMock(return_value=_ORG),
        ):
            with _client_with_conn(conn) as client:
                resp = client.post(
                    f"/knowledge/v1/artifacts/{_ARTIFACT}/reindex",
                    params={"org_id": _ORG},
                )

        assert resp.status_code == 404
        assert "artifact not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE upload — happy path (no user_id filter = owner delete)
# ---------------------------------------------------------------------------
class TestDeleteUploadHappyPath:
    def test_returns_204_without_user_id(self) -> None:
        conn = _make_mock_conn()
        # get_kb_upload_artifact returns a row
        conn.fetchrow = AsyncMock(
            return_value={
                "artifact_id": _ARTIFACT,
                "path": _PATH,
                "user_id": "user-abc",
            }
        )

        with (
            patch(
                "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
                AsyncMock(return_value=_ORG),
            ),
            patch(
                "knowledge_ingest.qdrant_store.delete_document",
                AsyncMock(return_value=None),
            ),
        ):
            with _client_with_conn(conn) as client:
                resp = client.delete(
                    f"/knowledge/v1/kb/{_KB}/uploads/{_ARTIFACT}",
                    params={"org_id": _ORG},
                )

        assert resp.status_code == 204

    def test_returns_204_with_matching_user_id(self) -> None:
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value={
                "artifact_id": _ARTIFACT,
                "path": _PATH,
                "user_id": "user-abc",
            }
        )

        with (
            patch(
                "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
                AsyncMock(return_value=_ORG),
            ),
            patch(
                "knowledge_ingest.qdrant_store.delete_document",
                AsyncMock(return_value=None),
            ),
        ):
            with _client_with_conn(conn) as client:
                resp = client.delete(
                    f"/knowledge/v1/kb/{_KB}/uploads/{_ARTIFACT}",
                    params={"org_id": _ORG, "user_id": "user-abc"},
                )

        assert resp.status_code == 204

    def test_calls_qdrant_delete_document(self) -> None:
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value={
                "artifact_id": _ARTIFACT,
                "path": _PATH,
                "user_id": "user-abc",
            }
        )
        qdrant_mock = AsyncMock(return_value=None)

        with (
            patch(
                "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
                AsyncMock(return_value=_ORG),
            ),
            patch("knowledge_ingest.qdrant_store.delete_document", qdrant_mock),
        ):
            with _client_with_conn(conn) as client:
                client.delete(
                    f"/knowledge/v1/kb/{_KB}/uploads/{_ARTIFACT}",
                    params={"org_id": _ORG},
                )

        qdrant_mock.assert_awaited_once_with(_ORG, _KB, _PATH)


# ---------------------------------------------------------------------------
# DELETE upload — 404
# ---------------------------------------------------------------------------
class TestDeleteUploadNotFound:
    def test_returns_404_when_artifact_missing(self) -> None:
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)

        with patch(
            "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
            AsyncMock(return_value=_ORG),
        ):
            with _client_with_conn(conn) as client:
                resp = client.delete(
                    f"/knowledge/v1/kb/{_KB}/uploads/{_ARTIFACT}",
                    params={"org_id": _ORG},
                )

        assert resp.status_code == 404
        assert "artifact not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE upload — 403 ownership mismatch
# ---------------------------------------------------------------------------
class TestDeleteUploadOwnershipMismatch:
    def test_returns_403_when_user_id_does_not_match(self) -> None:
        conn = _make_mock_conn()
        conn.fetchrow = AsyncMock(
            return_value={
                "artifact_id": _ARTIFACT,
                "path": _PATH,
                "user_id": "user-owner",
            }
        )

        with patch(
            "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
            AsyncMock(return_value=_ORG),
        ):
            with _client_with_conn(conn) as client:
                resp = client.delete(
                    f"/knowledge/v1/kb/{_KB}/uploads/{_ARTIFACT}",
                    params={"org_id": _ORG, "user_id": "user-intruder"},
                )

        assert resp.status_code == 403
        assert "only delete your own uploads" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Smoke: list_kb_sources returns index_status on uploads
# ---------------------------------------------------------------------------
class TestListKbSourcesIndexStatus:
    def test_upload_row_includes_index_status(self) -> None:
        """Smoke: the uploads sub-list carries index_status from the DB row."""
        conn = _make_mock_conn()

        # The row shape must match what list_kb_sources returns from its dict-build:
        # {"id", "path", "content_type", "created_at" (int), "chunks_count", "index_status"}
        upload_row = {
            "id": _ARTIFACT,
            "path": "uploads/file.pdf",
            "display_name": "file.pdf",
            "source_url": None,
            "content_type": "application/pdf",
            "created_at": 1751328000,
            "chunks_count": 5,
            "index_status": "pending",
        }
        # list_kb_sources makes two fetch calls: connectors then uploads.
        # Make the first call return [] (no connectors), second return the upload.
        call_count = 0

        async def _side_effect(sql: str, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # first call: connector rows
                return []
            return [upload_row]

        conn.fetch = AsyncMock(side_effect=_side_effect)

        with patch(
            "knowledge_ingest.routes.kb_sources.assert_caller_identity_tenant_only",
            AsyncMock(return_value=_ORG),
        ):
            with _client_with_conn(conn) as client:
                resp = client.get(
                    f"/knowledge/v1/kb/{_KB}/sources",
                    params={"org_id": _ORG},
                )

        assert resp.status_code == 200
        uploads = resp.json()["uploads"]
        assert len(uploads) == 1
        assert uploads[0]["index_status"] == "pending"
