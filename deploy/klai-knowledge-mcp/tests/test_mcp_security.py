"""Tests for MCP security: path traversal (TASK-011) and auth (TASK-003)."""
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Minimal mock for FastMCP Context
def _make_ctx(headers: dict | None = None):
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Set required environment variables for main.py import."""
    monkeypatch.setenv("KLAI_DOCS_API_BASE", "http://docs-app:3000")
    monkeypatch.setenv("DOCS_INTERNAL_SECRET", "docs-secret")
    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://knowledge-ingest:8000")
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret")


class TestPathTraversalValidation:
    """Tests for save_to_docs path traversal prevention (V009)."""

    @pytest.mark.asyncio
    async def test_kb_name_with_path_traversal_rejected(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        result = await save_to_docs(
            title="Test",
            content="content",
            ctx=ctx,
            kb_name="../../../etc/passwd",
            page_path="valid-page",
        )
        assert "Error" in result
        assert "invalid characters" in result

    @pytest.mark.asyncio
    async def test_kb_name_with_slash_rejected(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        result = await save_to_docs(
            title="Test",
            content="content",
            ctx=ctx,
            kb_name="kb/evil",
            page_path="valid-page",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_page_path_with_dotdot_rejected(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        result = await save_to_docs(
            title="Test",
            content="content",
            ctx=ctx,
            kb_name="valid-kb",
            page_path="../../etc/passwd",
        )
        assert "Error" in result
        assert "invalid path" in result

    @pytest.mark.asyncio
    async def test_page_path_with_backslash_rejected(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        result = await save_to_docs(
            title="Test",
            content="content",
            ctx=ctx,
            kb_name="valid-kb",
            page_path="path\\evil",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_page_path_starting_with_slash_rejected(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        result = await save_to_docs(
            title="Test",
            content="content",
            ctx=ctx,
            kb_name="valid-kb",
            page_path="/absolute-path",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_valid_kb_name_and_page_path_accepted(self, _patch_env):
        from main import save_to_docs

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-org-slug": "testorg",
            "x-internal-secret": "test-secret",
        })
        with patch("main.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"name": "docs", "slug": "docs"}]
            mock_resp.text = ""

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.put = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await save_to_docs(
                title="Test Page",
                content="Valid content",
                ctx=ctx,
                kb_name="my-docs",
                page_path="valid-page",
            )
            # Should not be an error
            assert "Error" not in result or "HTTP" in result  # May fail on HTTP but not validation


class TestIncomingAuth:
    """Tests for incoming X-Internal-Secret validation (V005)."""

    @pytest.mark.asyncio
    async def test_missing_secret_returns_error(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
        })
        result = await save_personal_knowledge(
            title="Test",
            content="content",
            assertion_mode="note",
            tags=["test"],
            ctx=ctx,
        )
        assert "Error" in result
        assert "X-Internal-Secret" in result

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_error(self, _patch_env):
        from main import save_org_knowledge

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-internal-secret": "wrong-secret",
        })
        result = await save_org_knowledge(
            title="Test",
            content="content",
            assertion_mode="note",
            tags=["test"],
            ctx=ctx,
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_valid_secret_passes_auth(self, _patch_env):
        from main import save_personal_knowledge

        ctx = _make_ctx({
            "x-user-id": "user1",
            "x-org-id": "org1",
            "x-internal-secret": "test-secret",
        })
        with patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True):
            result = await save_personal_knowledge(
                title="Test",
                content="content",
                assertion_mode="note",
                tags=["test"],
                ctx=ctx,
            )
            assert "Error" not in result
