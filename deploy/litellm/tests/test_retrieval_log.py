"""RED: Verify _fire_retrieval_log in klai_knowledge.py.

SPEC-KB-015 REQ-KB-015-01/02/03:
- Fire retrieval log after successful retrieval
- NOT fired when gate bypassed
- NOT fired when no chunk_ids
- Silent discard on non-numeric org_id
- Uses create_task (no added latency)
"""

import importlib
import sys
import types
from datetime import datetime, timezone
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

    for mod_name in ["litellm", "litellm.integrations", "litellm.integrations.custom_logger"]:
        sys.modules.pop(mod_name, None)
    sys.modules.pop("klai_knowledge", None)


def _load_hook(monkeypatch, extra_env=None):
    """Import and reload klai_knowledge with env vars."""
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    sys.modules.pop("klai_knowledge", None)
    import klai_knowledge
    importlib.reload(klai_knowledge)
    return klai_knowledge


def test_fire_retrieval_log_exists(monkeypatch):
    """Module must expose _fire_retrieval_log function."""
    mod = _load_hook(monkeypatch)
    assert hasattr(mod, "_fire_retrieval_log")


def test_fire_retrieval_log_skips_non_numeric_org(monkeypatch):
    """Non-numeric org_id -> skip silently, no create_task called."""
    mod = _load_hook(monkeypatch)

    mock_loop = MagicMock()
    with patch("asyncio.get_running_loop", return_value=mock_loop):
        mod._fire_retrieval_log(
            org_id="not-a-number",
            user_id="user123",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test query",
        )
        mock_loop.create_task.assert_not_called()


def test_fire_retrieval_log_calls_create_task(monkeypatch):
    """Valid org_id -> create_task is called (fire-and-forget)."""
    mod = _load_hook(monkeypatch)

    mock_loop = MagicMock()
    with patch("asyncio.get_running_loop", return_value=mock_loop):
        mod._fire_retrieval_log(
            org_id="42",
            user_id="user123",
            chunk_ids=["c1", "c2"],
            reranker_scores=[0.9, 0.8],
            query_resolved="test query",
        )
        mock_loop.create_task.assert_called_once()


def test_retrieval_log_url_constant(monkeypatch):
    """PORTAL_RETRIEVAL_LOG_URL must point to /internal/v1/retrieval-log."""
    mod = _load_hook(monkeypatch)
    assert "/internal/v1/retrieval-log" in mod.PORTAL_RETRIEVAL_LOG_URL


def test_embedding_model_version_constant(monkeypatch):
    """EMBEDDING_MODEL_VERSION defaults to bge-m3-v1."""
    mod = _load_hook(monkeypatch)
    assert mod.EMBEDDING_MODEL_VERSION == "bge-m3-v1"
