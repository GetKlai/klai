"""Tests for klai_knowledge.py (KB-010) and custom_router.py (AC-010-17).

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

    for mod_name in ["litellm", "litellm.integrations", "litellm.integrations.custom_logger"]:
        sys.modules.pop(mod_name, None)
    sys.modules.pop("klai_knowledge", None)


def _load_hook(monkeypatch, extra_env=None):
    """Import and reload klai_knowledge with the given env vars."""
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


def _make_cache(feature_enabled: bool | None = None):
    """Build a mock LiteLLM DualCache that returns a cached feature result."""
    cache = MagicMock()
    cached_value = None if feature_enabled is None else ("1" if feature_enabled else "0")
    cache.async_get_cache = AsyncMock(return_value=cached_value)
    cache.async_set_cache = AsyncMock()
    return cache


def _make_user_api_key(org_id="org123"):
    uak = MagicMock()
    uak.metadata = {"org_id": org_id}
    return uak


def _make_resp(json_data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _patch_http(monkeypatch, portal_resp=None, retrieval_resp=None):
    """Patch httpx.AsyncClient.get and .post for portal and retrieval calls."""
    async def _async_get(url, **kwargs):
        return portal_resp or _make_resp({"enabled": True})

    async def _async_post(url, **kwargs):
        return retrieval_resp or _make_resp({"chunks": [], "retrieval_bypassed": False})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_async_get)
    mock_client.post = AsyncMock(side_effect=_async_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("klai_knowledge.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


# ─── Legacy tests (preserved, updated for new hook) ─────────────────────────

class TestKlaiKnowledgeHookLegacy:
    @pytest.mark.asyncio
    async def test_retrieve_request_includes_auth_header(self, monkeypatch):
        """V001: retrieve request must include X-Internal-Secret header."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the team policies?"}
        ]}

        mock_resp = _make_resp({"chunks": []})
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            post_call = mc.post.call_args
            headers = post_call.kwargs.get("headers") or {}
            assert headers.get("X-Internal-Secret") == "test-portal-secret"

    @pytest.mark.asyncio
    async def test_no_secret_no_header(self, monkeypatch):
        """When PORTAL_INTERNAL_SECRET is empty, no auth header sent to retrieval."""
        mod = _load_hook(monkeypatch, extra_env={"PORTAL_INTERNAL_SECRET": ""})
        hook = mod.KlaiKnowledgeHook()
        # Cache says enabled=True so we skip the portal HTTP call
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the team guidelines and policies?"}
        ]}

        mock_resp = _make_resp({"chunks": []})
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            post_call = mc.post.call_args
            if post_call:
                headers = post_call.kwargs.get("headers") or {}
                assert "X-Internal-Secret" not in headers


# ─── KB-010 new tests ────────────────────────────────────────────────────────

class TestKlaiKnowledgeHookKB010:
    @pytest.mark.asyncio
    async def test_blocked_when_no_knowledge_feature(self, monkeypatch):
        """AC-010-01: user without entitlement gets no retrieval call."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=False)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat staat er in ons marketingbudget?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            mc.post.assert_not_called()
        assert "_klai_kb_meta" not in result

    @pytest.mark.asyncio
    async def test_blocked_when_no_user_id(self, monkeypatch):
        """AC-010-02: missing user field → no injection, no retrieval call."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache()

        data = {"messages": [{"role": "user", "content": "Vertel me over het project."}]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            mc.get.assert_not_called()
            mc.post.assert_not_called()
        assert "_klai_kb_meta" not in result

    @pytest.mark.asyncio
    async def test_blocked_when_portal_unreachable(self, monkeypatch):
        """AC-010-03: portal authz endpoint down → fail-closed, no injection."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        # No cached value forces a live HTTP call
        cache = _make_cache(feature_enabled=None)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Geef me een samenvatting van de Q1-cijfers."}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(side_effect=Exception("Connection refused"))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            mc.post.assert_not_called()
        assert "_klai_kb_meta" not in result

    @pytest.mark.asyncio
    async def test_feature_check_cached(self, monkeypatch):
        """AC-010-05: second call within TTL window skips portal HTTP call."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        # Cache already contains result → no HTTP needed
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is ons personeelsbeleid?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            # get() must NOT have been called (authz came from cache)
            mc.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_scope_and_user_id_in_request(self, monkeypatch):
        """AC-010-10: retrieval request includes scope='both' and user_id."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Toon me de vergadernotities van vorige week."}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            post_call = mc.post.call_args
            body = post_call.kwargs.get("json") or {}
            assert body.get("scope") == "both"
            assert body.get("user_id") == "aabbcc112233445566778899"

    @pytest.mark.asyncio
    async def test_conversation_history_passed(self, monkeypatch):
        """AC-010-12: conversation_history sent with up to 6 prior turns."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is het budget?"},
            {"role": "assistant", "content": "Het budget is 100k."},
            {"role": "user", "content": "Wie heeft dat besloten?"},
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs.get("json") or {}
            history = body.get("conversation_history", [])
            assert len(history) == 2
            assert history[0]["role"] == "user"
            assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_gate_bypass_no_injection(self, monkeypatch):
        """AC-010-11: retrieval_bypassed=True → no chunks injected, meta recorded."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat zijn onze bedrijfswaarden?"}
        ]}

        retrieval_resp = _make_resp({"chunks": [], "retrieval_bypassed": True})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

        system_msgs = [m for m in result.get("messages", []) if m.get("role") == "system"]
        assert not system_msgs
        assert result["_klai_kb_meta"]["gate_bypassed"] is True

    @pytest.mark.asyncio
    async def test_provenance_labels(self, monkeypatch):
        """AC-010-14: injected chunks have [org] or [persoonlijk] labels."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is de status van project Alpha?"}
        ]}

        chunks = [
            {"text": "Org chunk tekst.", "scope": "org", "metadata": {"title": "Org doc"}},
            {"text": "Persoonlijke notitie.", "scope": "personal", "metadata": {"title": "Mijn notitie"}},
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

        system_content = result["messages"][0]["content"]
        assert "[org]" in system_content
        assert "[persoonlijk]" in system_content

    @pytest.mark.asyncio
    async def test_kb_meta_logged(self, monkeypatch):
        """AC-010-16: _klai_kb_meta set on data after successful injection."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Geef een overzicht van de Q2-resultaten."}
        ]}

        chunks = [{"text": "Q2 resultaten waren positief.", "scope": "org", "metadata": {}}]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

        meta = result.get("_klai_kb_meta")
        assert meta is not None
        assert meta["org_id"] == "org123"
        assert meta["user_id"] == "aabbcc112233445566778899"
        assert meta["chunks_injected"] == 1
        assert meta["gate_bypassed"] is False


# ─── Token router test ────────────────────────────────────────────────────────

class TestTokenRouterKB010:
    @pytest.mark.asyncio
    async def test_token_router_skips_downgrade_when_kb_injected(self, monkeypatch):
        """AC-010-17: model stays klai-primary when _klai_kb_meta present, even > 3000 tokens."""
        # Mock litellm token_counter to return a high count
        litellm_mod = sys.modules["litellm"]
        litellm_mod.token_counter = MagicMock(return_value=4000)

        sys.modules.pop("custom_router", None)
        import custom_router
        importlib.reload(custom_router)

        router = custom_router.TokenRouter()
        uak = MagicMock()

        # Simulate 4000 tokens worth of messages with KB meta set
        messages = [{"role": "user", "content": "x" * 100}]
        data = {
            "model": "klai-primary",
            "messages": messages,
            "_klai_kb_meta": {"org_id": "org1", "user_id": "u1", "chunks_injected": 3},
        }

        result = await router.async_pre_call_hook(uak, None, data, "completion")
        assert result["model"] == "klai-primary"
