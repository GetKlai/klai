"""Tests for litellm klai_knowledge.py security fixes (TASK-004, TASK-005).

litellm is not installed locally (runs in Docker), so we mock the import.
"""
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Mock litellm module so klai_knowledge can be imported."""
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass
        async def async_post_call_success_hook(self, *args, **kwargs):
            pass
        async def async_post_call_failure_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield

    # Cleanup
    for mod_name in ["litellm", "litellm.integrations", "litellm.integrations.custom_logger"]:
        sys.modules.pop(mod_name, None)
    # Force reimport on next test
    sys.modules.pop("klai_knowledge", None)


class TestKlaiKnowledgeHook:
    @pytest.mark.asyncio
    async def test_retrieve_request_includes_kb_slugs_org(self, monkeypatch):
        """V006: retrieve request must include kb_slugs: ["org"]."""
        monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-hook-secret")

        # Force reimport
        sys.modules.pop("klai_knowledge", None)
        import klai_knowledge
        importlib.reload(klai_knowledge)

        hook = klai_knowledge.KlaiKnowledgeHook()

        user_api_key_dict = MagicMock()
        user_api_key_dict.metadata = {"org_id": "org123"}

        data = {
            "messages": [
                {"role": "user", "content": "What are the team policies?"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chunks": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("klai_knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await hook.async_pre_call_hook(user_api_key_dict, None, data, "completion")

            post_call = mock_client.post.call_args
            request_json = post_call.kwargs.get("json") or post_call[1].get("json")
            assert request_json["kb_slugs"] == ["org"]

    @pytest.mark.asyncio
    async def test_retrieve_request_includes_auth_header(self, monkeypatch):
        """V001: retrieve request must include X-Internal-Secret header."""
        monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-hook-secret")

        sys.modules.pop("klai_knowledge", None)
        import klai_knowledge
        importlib.reload(klai_knowledge)

        hook = klai_knowledge.KlaiKnowledgeHook()

        user_api_key_dict = MagicMock()
        user_api_key_dict.metadata = {"org_id": "org123"}

        data = {
            "messages": [
                {"role": "user", "content": "Tell me about our knowledge base policies"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chunks": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("klai_knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await hook.async_pre_call_hook(user_api_key_dict, None, data, "completion")

            post_call = mock_client.post.call_args
            request_headers = post_call.kwargs.get("headers") or post_call[1].get("headers")
            assert request_headers.get("X-Internal-Secret") == "test-hook-secret"

    @pytest.mark.asyncio
    async def test_no_secret_no_header(self, monkeypatch):
        """When KNOWLEDGE_INGEST_SECRET is empty, no auth header should be sent."""
        monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "")

        sys.modules.pop("klai_knowledge", None)
        import klai_knowledge
        importlib.reload(klai_knowledge)

        hook = klai_knowledge.KlaiKnowledgeHook()

        user_api_key_dict = MagicMock()
        user_api_key_dict.metadata = {"org_id": "org123"}

        data = {
            "messages": [
                {"role": "user", "content": "What are the team guidelines and policies?"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"chunks": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("klai_knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await hook.async_pre_call_hook(user_api_key_dict, None, data, "completion")

            post_call = mock_client.post.call_args
            request_headers = post_call.kwargs.get("headers") or post_call[1].get("headers")
            assert "X-Internal-Secret" not in request_headers
