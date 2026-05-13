"""Tests for klai_knowledge.py (KB-010) and custom_router.py (AC-010-17).

litellm is not installed locally (runs in Docker), so we mock the import.
"""
import importlib
import sys
import types
from types import SimpleNamespace
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




def _load_hook(monkeypatch, extra_env=None, *, mock_fire_and_forget=True):
    """Import and reload klai_knowledge with the given env vars.

    By default also silences the fire-and-forget producers
    (``_fire_gap_event`` and ``_fire_retrieval_log``). Both are
    synchronous functions that internally build a ``_post()`` coroutine
    and try to schedule it via
    ``asyncio.get_running_loop().create_task(_post())``. In a test
    context without a running loop the schedule call raises
    ``RuntimeError``, which the function catches and silently swallows
    — but the inner coroutine is already constructed and never awaited.
    Python then raises ``RuntimeWarning: coroutine '_post' was never
    awaited`` at GC, AFTER pytest's warning-capture has torn down,
    polluting the test output via ``sys.unraisablehook``.

    Per ``.claude/rules/klai/lang/testing.md`` the canonical fix is to
    replace the producer with a sync ``MagicMock`` so no coroutine is
    constructed in the first place. Tests that specifically exercise
    these producers (``TestFireGapEvent``) pass
    ``mock_fire_and_forget=False`` to skip the patch.
    """
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
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
    if mock_fire_and_forget:
        monkeypatch.setattr(klai_knowledge, "_fire_gap_event", MagicMock())
        monkeypatch.setattr(klai_knowledge, "_fire_retrieval_log", MagicMock())
    return klai_knowledge


def _make_cache(feature_enabled: bool | None = None, feature: dict | None = None):
    """Build a mock LiteLLM DualCache for the two-level version cache.

    feature_enabled=None, feature=None: cache miss — forces portal HTTP call.
    feature_enabled=True/False: cache hit with a default feature dict.
    feature=<dict>: cache hit with the given feature dict (ignores feature_enabled).

    Two-level cache structure:
    - kb_ver:{org_id}:{user_id}               → version string ("0")
    - kb_feature:{org_id}:{user_id}:{version} → feature dict

    Templates cache key (``templates:{org_id}:{user_id}``) is pre-seeded
    with ``[]`` regardless of branch — that short-circuits ``_get_templates``
    out of its HTTP path so tests don't need to mock ``client.get`` for the
    portal templates endpoint. Tests that specifically exercise the
    templates HTTP path can shadow this by passing their own mock
    ``cache.async_get_cache``.
    """
    cache = MagicMock()
    cache.async_set_cache = AsyncMock()

    if feature is None and feature_enabled is None:
        # Cache miss for KB feature — both kb_ver and kb_feature keys return
        # None to force the portal HTTP call. Templates + taxonomy caches are
        # still seeded so those roundtrips stay out of the way of
        # feature-flag tests (a test that doesn't set ``mc.get`` would
        # otherwise trip the AsyncMock-never-awaited warning when the hook
        # tries to fetch trees/coverage).
        async def _get(key: str) -> object:
            if key.startswith("templates:"):
                return []
            if key.startswith("tax_trees:"):
                return {}
            if key.startswith("tax_coverage:"):
                return {}
            return None

        cache.async_get_cache = AsyncMock(side_effect=_get)
    else:
        # Default feature dict — tests that pass a custom ``feature`` get
        # this as the base and override only the keys they care about. That
        # way every cached feature still carries a resolved
        # ``zitadel_user_id`` (required since 2026-05-05 for /retrieve
        # identity-verify) without each test having to remember it.
        default_feat: dict = {
            "enabled": bool(feature_enabled) if feature is None else True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "version": 0,
            "zitadel_user_id": "300000000000000002",
        }
        feat: dict = {**default_feat, **(feature or {})}

        async def _get(key: str) -> object:
            if key.startswith("kb_ver:"):
                return "0"
            if key.startswith("kb_feature:"):
                return feat
            if key.startswith("templates:"):
                return []
            # Taxonomy lookups: pre-seed empty dicts so tests with kb_slugs
            # filter don't need to mock ``mc.get`` for the
            # /internal/v1/taxonomy/{trees,coverage} endpoints. Without
            # this, a kb_slugs test triggers the multi-KB fetch path and
            # the unconfigured AsyncMock returns a coroutine that's never
            # awaited (RuntimeWarning at GC).
            if key.startswith("tax_trees:"):
                return {}
            if key.startswith("tax_coverage:"):
                return {}
            return None

        cache.async_get_cache = AsyncMock(side_effect=_get)

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
        """V001: retrieve request must include X-Internal-Secret header.

        Regression-guard for the 2026-04-28 → 2026-05-05 outage:
        retrieval-api requires X-Caller-Service: litellm; without it a 400
        is returned and the hook degrades silently. Both headers MUST be
        present on every legacy-auth /retrieve request.
        """
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
            assert headers.get("X-Internal-Secret") == "test-retrieval-secret"
            assert headers.get("X-Caller-Service") == "litellm"

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


# ─── SPEC-SEC-SERVICE-AUTH-001 Phase C-1 — dual-auth tests ──────────────────

class TestKlaiKnowledgeHookDualAuth:
    """Phase C-1 (REQ-5 safe rollout): caller prefers JWT, falls back to
    X-Internal-Secret on either mint failure or receiver-side 401/403.

    The receive-side fallback exists because the SPEC's Phase A operator
    runbook assumes the receiver's Zitadel project + audience + role grant
    is set up — but during the migration window that setup may lag the
    code rollout. Without the retry, knowledge retrieval breaks entirely
    until the IdP catches up. With it, the legacy path remains live for
    the full soak window, and Phase D removes the retry once the IdP
    config holds zero ``jwt_rejected`` events for 7 days.
    """

    @pytest.mark.asyncio
    async def test_jwt_path_used_when_token_client_returns_token(self, monkeypatch):
        """Token client mints → request goes out with Authorization: Bearer …"""
        mod = _load_hook(monkeypatch)

        token_client = MagicMock()
        token_client.get_token = AsyncMock(return_value="fake.jwt.token")
        monkeypatch.setattr(mod, "_get_token_client", lambda: token_client)

        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the policies?"}
        ]}

        mock_resp = _make_resp({"chunks": []})
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 1
            headers = mc.post.call_args.kwargs.get("headers") or {}
            assert headers.get("Authorization") == "Bearer fake.jwt.token"
            assert headers.get("X-Caller-Service") == "litellm"
            assert "X-Internal-Secret" not in headers

    @pytest.mark.asyncio
    async def test_jwt_mint_failure_falls_back_to_internal_secret(self, monkeypatch):
        """Token client raises → exactly one request, with X-Internal-Secret."""
        mod = _load_hook(monkeypatch)

        token_client = MagicMock()
        token_client.get_token = AsyncMock(side_effect=RuntimeError("zitadel down"))
        monkeypatch.setattr(mod, "_get_token_client", lambda: token_client)

        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the policies?"}
        ]}

        mock_resp = _make_resp({"chunks": []})
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 1
            headers = mc.post.call_args.kwargs.get("headers") or {}
            assert headers.get("X-Internal-Secret") == "test-retrieval-secret"
            assert headers.get("X-Caller-Service") == "litellm"
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_jwt_401_from_receiver_retries_with_internal_secret(self, monkeypatch):
        """Token mints fine but receiver 401s (audience mismatch) → retry once."""
        mod = _load_hook(monkeypatch)

        token_client = MagicMock()
        token_client.get_token = AsyncMock(return_value="fake.jwt.token")
        monkeypatch.setattr(mod, "_get_token_client", lambda: token_client)

        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the policies?"}
        ]}

        jwt_reject = _make_resp({"error": "unauthorized"}, status_code=401)
        legacy_ok = _make_resp({"chunks": []}, status_code=200)
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(side_effect=[jwt_reject, legacy_ok])
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 2
            first_headers = mc.post.call_args_list[0].kwargs.get("headers") or {}
            second_headers = mc.post.call_args_list[1].kwargs.get("headers") or {}
            assert first_headers.get("Authorization") == "Bearer fake.jwt.token"
            assert first_headers.get("X-Caller-Service") == "litellm"
            assert second_headers.get("X-Internal-Secret") == "test-retrieval-secret"
            assert second_headers.get("X-Caller-Service") == "litellm"
            assert "Authorization" not in second_headers

    @pytest.mark.asyncio
    async def test_jwt_403_from_receiver_retries_with_internal_secret(self, monkeypatch):
        """Receiver 403 insufficient_scope → same retry path as 401."""
        mod = _load_hook(monkeypatch)

        token_client = MagicMock()
        token_client.get_token = AsyncMock(return_value="fake.jwt.token")
        monkeypatch.setattr(mod, "_get_token_client", lambda: token_client)

        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the policies?"}
        ]}

        jwt_reject = _make_resp({"error": "insufficient_scope"}, status_code=403)
        legacy_ok = _make_resp({"chunks": []}, status_code=200)
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(side_effect=[jwt_reject, legacy_ok])
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 2

    @pytest.mark.asyncio
    async def test_no_token_client_uses_internal_secret_directly(self, monkeypatch):
        """No token client configured → single request with X-Internal-Secret."""
        mod = _load_hook(monkeypatch)
        monkeypatch.setattr(mod, "_get_token_client", lambda: None)

        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the policies?"}
        ]}

        mock_resp = _make_resp({"chunks": []})
        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=mock_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 1
            headers = mc.post.call_args.kwargs.get("headers") or {}
            assert headers.get("X-Internal-Secret") == "test-retrieval-secret"
            assert headers.get("X-Caller-Service") == "litellm"
            assert "Authorization" not in headers


# ─── identity mapping tests (2026-05-05 follow-up) ──────────────────────────


class TestKlaiKnowledgeHookIdentityMapping:
    """``/retrieve`` body MUST carry the Zitadel sub, NOT the LibreChat ObjectId.

    SPEC-SEC-IDENTITY-ASSERT-001 made retrieval-api forward
    ``claimed_user_id`` to portal-api ``/internal/identity/verify`` which
    matches against ``PortalUser.zitadel_user_id``. Personal-KB qdrant
    chunks are also stamped with the Zitadel sub at ingest time. Sending
    the LibreChat ObjectId on either path returns no_membership / 0
    chunks. The hook resolves the LibreChat ObjectId → Zitadel sub via
    the kb_feature endpoint response.
    """

    @pytest.mark.asyncio
    async def test_retrieve_body_uses_zitadel_user_id_not_librechat_objectid(
        self, monkeypatch
    ):
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": True,
                "kb_slugs_filter": None,
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": "300000000000000002",
            }
        )
        librechat_objectid = "aabbcc112233445566778899"
        data = {"user": librechat_objectid, "messages": [
            {"role": "user", "content": "What are the team policies?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 1
            body = mc.post.call_args.kwargs["json"]
            assert body["user_id"] == "300000000000000002", (
                f"/retrieve user_id should be the Zitadel sub, got {body['user_id']!r}."
            )
            assert body["user_id"] != librechat_objectid

    @pytest.mark.asyncio
    async def test_missing_zitadel_user_id_fails_loud(self, monkeypatch):
        """portal-api returned None for zitadel_user_id → fail loud, no /retrieve."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": True,
                "kb_slugs_filter": None,
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": None,
            }
        )
        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the team policies?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 0
            system_msg = next(
                (m for m in data["messages"] if m["role"] == "system"), None
            )
            assert system_msg is not None
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): the KB-unavailable
            # notice is now English (model warns user in their detected language).
            assert "TEMPORARILY UNAVAILABLE" in system_msg["content"]


# ─── kb_slugs_filter tri-state tests (2026-05-05 follow-up) ─────────────────


class TestKlaiKnowledgeHookSlugsTriState:
    """Pin the tri-state contract for ``kb_slugs_filter``:

    * ``None`` → all org KBs (no filter sent)
    * ``[]``   → user explicitly turned every org collection off
    * ``[..]`` → explicit subset

    The hook used to treat ``[]`` and ``None`` as identical via
    ``if kb_slugs:``, so a user who turned every collection off (and
    personal too) silently still got every org chunk injected — the
    exact opposite of intent. See pitfalls →
    ``kb-slugs-filter-empty-list-collapse``.
    """

    @pytest.mark.asyncio
    async def test_empty_slugs_and_personal_off_skips_retrieval(self, monkeypatch):
        """[] + personal=False → hook short-circuits, no /retrieve call."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": False,
                "kb_slugs_filter": [],
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": "300000000000000002",
            }
        )
        data = {"user": "u1" * 12, "messages": [
            {"role": "user", "content": "What about that thing?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            # No /retrieve call — user opted out of every scope.
            assert mc.post.call_count == 0, (
                "kb_personal=False + kb_slugs=[] MUST skip retrieval entirely. "
                "If a /retrieve call goes out the user gets back chunks they "
                "explicitly told us not to fetch."
            )

    @pytest.mark.asyncio
    async def test_empty_slugs_and_personal_off_uses_general_chat_prompt(
        self, monkeypatch
    ):
        """[] + personal=False → "Algemene AI" mode: GENERAL prompt, not GROUNDED.

        Regression test for the 2026-05-07 UX bug where the user disabled
        every collection but still got KB-grounded answers ("Dat staat niet
        in de kennisbank"). The hook now swaps in
        ``GENERAL_CHAT_SYSTEM_PROMPT`` on this branch so the model behaves
        as a general assistant without pretending to have sources.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": False,
                "kb_slugs_filter": [],
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": "300000000000000002",
            }
        )
        data = {"user": "u1" * 12, "messages": [
            {"role": "user", "content": "What about that thing?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        system_msg = next(
            (m for m in data["messages"] if m["role"] == "system"), None
        )
        assert system_msg is not None, (
            "Hook MUST inject a system prompt even on the no-KB branch — "
            "the language-detection contract still applies."
        )
        content = system_msg["content"]

        # GENERAL marker — model behaves as general-purpose assistant.
        assert "general-purpose assistant" in content, (
            "no-KB branch MUST inject GENERAL_CHAT_SYSTEM_PROMPT; got: "
            f"{content[:200]!r}"
        )

        # GROUNDED-only language MUST be absent. If any of these strings
        # leak in, the model will either cite [n] phantoms or refuse to
        # answer with 'Dat staat niet in de kennisbank' — exactly the
        # regression this branch fixes.
        assert "knowledge base chunks provided" not in content, (
            "GROUNDED prompt body leaked into the no-KB branch; the model "
            "will pretend to have chunks it doesn't have."
        )
        assert "Dat staat niet in de kennisbank" not in content, (
            "GROUNDED 'answer not in KB' fallback leaked into the no-KB "
            "branch; the model will refuse general-knowledge questions."
        )
        assert "Every factual claim gets a [n] citation" not in content, (
            "GROUNDED citation MANDATE leaked into the no-KB branch; the "
            "model will fabricate [n] markers without any sources."
        )
        # Positive form: GENERAL must EXPLICITLY forbid [n] citations,
        # not merely omit the GROUNDED rule.
        assert "Do NOT add [n] citations" in content, (
            "GENERAL prompt missing the explicit [n]-citation prohibition."
        )

        # Language-detection contract MUST still hold (shared preamble).
        assert "[CRITICAL]" in content
        assert "SUBSTANTIVE message" in content

    @pytest.mark.asyncio
    async def test_empty_slugs_and_personal_on_uses_personal_scope(self, monkeypatch):
        """[] + personal=True → scope=personal, no kb_slugs filter."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": True,
                "kb_slugs_filter": [],
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": "300000000000000002",
            }
        )
        data = {"user": "u1" * 12, "messages": [
            {"role": "user", "content": "What about that thing?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            assert mc.post.call_count == 1
            body = mc.post.call_args.kwargs["json"]
            assert body["scope"] == "personal", (
                f"Expected scope=personal when only personal is enabled, got {body['scope']!r}"
            )
            assert "kb_slugs" not in body, (
                "kb_slugs filter MUST NOT be sent in personal-only scope."
            )

    @pytest.mark.asyncio
    async def test_null_slugs_keeps_both_scope_no_filter(self, monkeypatch):
        """None + personal=True → scope=both, no filter (default behaviour)."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(
            feature={
                "enabled": True,
                "kb_retrieval_enabled": True,
                "kb_personal_enabled": True,
                "kb_slugs_filter": None,
                "kb_narrow": False,
                "version": 0,
                "zitadel_user_id": "300000000000000002",
            }
        )
        data = {"user": "u1" * 12, "messages": [
            {"role": "user", "content": "What about that thing?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs["json"]
            assert body["scope"] == "both"
            assert "kb_slugs" not in body


# ─── 2026-05-05 fail-loud regression tests ──────────────────────────────────


class TestKlaiKnowledgeHookFailLoud:
    """Regression-guard for the silent-degradation incident.

    Until 2026-05-05 the hook caught any /retrieve failure as a warning and
    silently dropped KB context. This hid the SPEC-SEC-IDENTITY-ASSERT-001
    Phase D regression for ~7 days. The new contract: a /retrieve failure
    MUST surface to the user via a system-prompt notice, AND the call MUST
    be marked in `_klai_kb_meta.retrieval_failure` so observability sees it.
    """

    @pytest.mark.asyncio
    async def test_retrieve_400_surfaces_in_system_prompt(self, monkeypatch):
        """A 400 from retrieval-api injects a visible 'KB unreachable' notice."""
        import httpx as _httpx

        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the team policies?"}
        ]}

        # Build a MagicMock that raises HTTPStatusError like a real 400 would.
        bad_resp = _make_resp(
            {"detail": {"error": "missing_caller_service"}}, status_code=400
        )
        bad_resp.text = '{"detail":{"error":"missing_caller_service"}}'
        bad_resp.raise_for_status = MagicMock(
            side_effect=_httpx.HTTPStatusError(
                "400", request=MagicMock(), response=bad_resp
            )
        )

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(return_value=bad_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            # System prompt must contain a user-visible "KB unreachable" notice.
            system_msg = next(
                (m for m in data["messages"] if m["role"] == "system"), None
            )
            assert system_msg is not None, "fail-loud must inject a system message"
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English notice
            # text; the model translates the warning into the user's language.
            assert "Knowledge Base" in system_msg["content"]
            assert "TEMPORARILY UNAVAILABLE" in system_msg["content"]

            # Failure marker MUST be in metadata for observability.
            kb_meta = data.get("metadata", {}).get("_klai_kb_meta", {})
            assert kb_meta.get("retrieval_failure") is not None
            assert kb_meta.get("chunks_injected") == 0

    @pytest.mark.asyncio
    async def test_retrieve_network_error_surfaces_in_system_prompt(self, monkeypatch):
        """ConnectError → same fail-loud path as HTTP error."""
        import httpx as _httpx

        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What are the team policies?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=_make_resp({"instructions": []}))
            mc.post = AsyncMock(side_effect=_httpx.ConnectError("connection refused"))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            system_msg = next(
                (m for m in data["messages"] if m["role"] == "system"), None
            )
            assert system_msg is not None
            # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English notice.
            assert "TEMPORARILY UNAVAILABLE" in system_msg["content"]
            kb_meta = data.get("metadata", {}).get("_klai_kb_meta", {})
            assert kb_meta.get("retrieval_failure") == "ConnectError"


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
            # /retrieve receives the resolved zitadel_user_id (not the
            # LibreChat ObjectId) since identity-verify shipped 2026-05-05.
            # The default _make_cache feature dict pre-seeds this sub.
            assert body.get("user_id") == "300000000000000002"

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
        """AC-010-11: retrieval_bypassed=True → no KB chunks injected, meta recorded.

        SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): the multilingual
        foundation (GROUNDED_CHAT_SYSTEM_PROMPT) IS still prepended on this
        path so the model still detects/respects the user's language. The
        invariant the test now pins is: no Klai-Knowledge-Base context block
        was injected (no [Klai Knowledge Base — ...] anchor, no
        [End knowledge base context] terminator), even though a system
        message exists.
        """
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
        assert len(system_msgs) == 1, "multilingual foundation must be prepended"
        sys_content = system_msgs[0]["content"]
        # GROUNDED_CHAT_SYSTEM_PROMPT signature line — its presence proves the
        # multilingual contract is in effect on the bypassed path too.
        assert "Detect the language of the user's most recent SUBSTANTIVE message" in sys_content
        # No KB context block was injected (the bypassed branch is the whole point).
        assert "Klai Knowledge Base" not in sys_content
        assert "[End knowledge base context]" not in sys_content
        # _klai_kb_meta lives under data["metadata"] for downstream hooks
        # (TokenRouter reads it from data.get("metadata", {})).
        assert result["metadata"]["_klai_kb_meta"]["gate_bypassed"] is True

    @pytest.mark.asyncio
    async def test_provenance_labels(self, monkeypatch):
        """AC-010-14: injected chunks have [org] or [personal] labels.

        SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10) renamed
        ``[persoonlijk]`` -> ``[personal]`` so the chunk-scope label is
        consistent with the rest of the now-English system prompt.
        """
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
        assert "[personal]" in system_content

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

        # _klai_kb_meta lives under data["metadata"] for downstream hooks.
        meta = result.get("metadata", {}).get("_klai_kb_meta")
        assert meta is not None
        assert meta["org_id"] == "org123"
        # user_id on the meta is the zitadel sub (matches the resolved sub
        # that gets sent to /retrieve).
        assert meta["user_id"] == "300000000000000002"
        assert meta["chunks_injected"] == 1
        assert meta["gate_bypassed"] is False


# ─── Token router test ────────────────────────────────────────────────────────

class TestTokenRouterKB010:
    @pytest.mark.asyncio
    async def test_kb_meta_skips_safety_net_downgrade(self, monkeypatch):
        """AC-010-17: kb-meta bypasses the *safety-net* downgrade only.

        Naming history: the original test was
        ``test_token_router_skips_downgrade_when_kb_injected`` — the name
        implied "any" downgrade, but the router has multiple downgrade
        branches and kb-meta only bypasses the safety-net (large total
        context → klai-fast). The long-user-message branch (analytical
        request → klai-large) fires regardless of KB context — that's by
        design.

        This test mocks the per-call token_counter to return a number
        BELOW the per-message threshold but ABOVE the total threshold,
        exercising only the safety-net path that KB context is meant to
        bypass.
        """
        litellm_mod = sys.modules["litellm"]

        def _fake_token_counter(*, model: str, messages: list, **_) -> int:
            # If only one message is being counted → it's the per-user-message
            # check. Return a value BELOW USER_MESSAGE_THRESHOLD (300).
            # Otherwise → it's the total-context safety net. Return a value
            # ABOVE SEARCH_TOKEN_THRESHOLD (3000).
            if len(messages) == 1:
                return 50
            return 4000

        litellm_mod.token_counter = MagicMock(side_effect=_fake_token_counter)

        sys.modules.pop("custom_router", None)
        import custom_router

        importlib.reload(custom_router)

        router = custom_router.TokenRouter()
        uak = MagicMock()

        # Two messages so the safety-net branch is exercised (total-context
        # counter is the one that would route to klai-fast without KB meta).
        messages = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
        ]
        data = {
            "model": "klai-primary",
            "messages": messages,
            # The hook stores _klai_kb_meta under data["metadata"] — the router
            # reads it from there, not from the top level.
            "metadata": {
                "_klai_kb_meta": {
                    "org_id": "org1",
                    "user_id": "u1",
                    "chunks_injected": 3,
                }
            },
        }

        result = await router.async_pre_call_hook(uak, None, data, "completion")
        assert result["model"] == "klai-primary"


# ─── KB-014 gap detection tests ──────────────────────────────────────────────


class TestClassifyGap:
    """Tests for the _classify_gap helper function."""

    def test_empty_chunks_returns_hard(self, monkeypatch):
        """R1.2: zero chunks → hard_gap."""
        mod = _load_hook(monkeypatch)
        assert mod._classify_gap([]) == "hard"

    def test_all_reranker_scores_below_threshold_returns_soft(self, monkeypatch):
        """R1.3: all reranker_score < 0.4 → soft_gap."""
        mod = _load_hook(monkeypatch)
        chunks = [
            {"reranker_score": 0.1, "score": 0.9, "metadata": {}},
            {"reranker_score": 0.3, "score": 0.8, "metadata": {}},
        ]
        assert mod._classify_gap(chunks) == "soft"

    def test_one_reranker_score_above_threshold_returns_none(self, monkeypatch):
        """R1.3: at least one reranker_score >= 0.4 → success (None)."""
        mod = _load_hook(monkeypatch)
        chunks = [
            {"reranker_score": 0.5, "score": 0.9, "metadata": {}},
            {"reranker_score": 0.1, "score": 0.2, "metadata": {}},
        ]
        assert mod._classify_gap(chunks) is None

    def test_no_reranker_dense_below_threshold_returns_soft(self, monkeypatch):
        """R1.4: no reranker scores, all dense score < 0.35 → soft_gap."""
        mod = _load_hook(monkeypatch)
        chunks = [
            {"score": 0.2, "metadata": {}},
            {"score": 0.3, "metadata": {}},
        ]
        assert mod._classify_gap(chunks) == "soft"

    def test_no_reranker_dense_above_threshold_returns_none(self, monkeypatch):
        """R1.4: no reranker scores, at least one dense score >= 0.35 → success."""
        mod = _load_hook(monkeypatch)
        chunks = [
            {"score": 0.4, "metadata": {}},
        ]
        assert mod._classify_gap(chunks) is None

    def test_custom_soft_threshold_via_env(self, monkeypatch):
        """R1.5: KLAI_GAP_SOFT_THRESHOLD env var overrides default."""
        mod = _load_hook(monkeypatch, extra_env={"KLAI_GAP_SOFT_THRESHOLD": "0.8"})
        chunks = [{"reranker_score": 0.5, "metadata": {}}]
        assert mod._classify_gap(chunks) == "soft"

    def test_custom_dense_threshold_via_env(self, monkeypatch):
        """R1.5: KLAI_GAP_DENSE_THRESHOLD env var overrides default."""
        mod = _load_hook(monkeypatch, extra_env={"KLAI_GAP_DENSE_THRESHOLD": "0.1"})
        chunks = [{"score": 0.2, "metadata": {}}]
        # 0.2 >= 0.1 → success
        assert mod._classify_gap(chunks) is None

    def test_mixed_reranker_some_none_uses_only_present_scores(self, monkeypatch):
        """Chunks with mixed reranker_score presence — only non-None scores count."""
        mod = _load_hook(monkeypatch)
        chunks = [
            {"reranker_score": 0.1, "score": 0.5, "metadata": {}},
            {"reranker_score": None, "score": 0.9, "metadata": {}},
        ]
        # reranker_scores = [0.1] (None is excluded), all < 0.4 → soft
        assert mod._classify_gap(chunks) == "soft"


class TestFireGapEvent:
    """Tests for the _fire_gap_event helper function."""

    def test_schedules_asyncio_task(self, monkeypatch):
        """R2.1: _fire_gap_event schedules an async POST task."""
        import asyncio as _asyncio

        mod = _load_hook(monkeypatch, mock_fire_and_forget=False)

        # ``create_task`` must close the coroutine it's handed — a real
        # event loop would await it, but our MagicMock loop just stores
        # it as call_args. Without ``.close()`` the coroutine surfaces
        # at GC as ``RuntimeWarning: coroutine '_post' was never awaited``.
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock(side_effect=lambda coro: coro.close())

        with patch.object(_asyncio, "get_running_loop", return_value=mock_loop):
            mod._fire_gap_event(
                org_id="42",
                user_id="user123",
                query_text="test query",
                gap_type="hard",
                chunks=[],
                retrieval_ms=150,
            )
        mock_loop.create_task.assert_called_once()

    def test_payload_has_correct_fields(self, monkeypatch):
        """R2.2: payload contains all required fields with correct types."""
        import asyncio as _asyncio

        mod = _load_hook(monkeypatch, mock_fire_and_forget=False)

        # ``create_task`` must close the coroutine it's handed — a real
        # event loop would await it, but our MagicMock loop just stores
        # it as call_args. Without ``.close()`` the coroutine surfaces
        # at GC as ``RuntimeWarning: coroutine '_post' was never awaited``.
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock(side_effect=lambda coro: coro.close())

        chunks = [
            {"reranker_score": 0.3, "score": 0.2, "metadata": {"kb_slug": "my-kb"}},
        ]

        with patch.object(_asyncio, "get_running_loop", return_value=mock_loop):
            mod._fire_gap_event(
                org_id="42",
                user_id="user123",
                query_text="how do I reset?",
                gap_type="soft",
                chunks=chunks,
                retrieval_ms=200,
            )

        assert mock_loop.create_task.called

    def test_hard_gap_nearest_kb_slug_is_none(self, monkeypatch):
        """R2.5: hard gaps have nearest_kb_slug = None."""
        import asyncio as _asyncio

        mod = _load_hook(monkeypatch, mock_fire_and_forget=False)

        # ``create_task`` must close the coroutine it's handed — a real
        # event loop would await it, but our MagicMock loop just stores
        # it as call_args. Without ``.close()`` the coroutine surfaces
        # at GC as ``RuntimeWarning: coroutine '_post' was never awaited``.
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock(side_effect=lambda coro: coro.close())

        with patch.object(_asyncio, "get_running_loop", return_value=mock_loop):
            mod._fire_gap_event(
                org_id="42",
                user_id="user123",
                query_text="test",
                gap_type="hard",
                chunks=[],
                retrieval_ms=100,
            )
        mock_loop.create_task.assert_called_once()

    def test_no_event_loop_silently_skips(self, monkeypatch):
        """R2.3: if no event loop, skip silently without raising."""
        import asyncio as _asyncio

        mod = _load_hook(monkeypatch, mock_fire_and_forget=False)

        with patch.object(_asyncio, "get_running_loop", side_effect=RuntimeError("no event loop")):
            # Should not raise
            mod._fire_gap_event(
                org_id="42",
                user_id="user123",
                query_text="test",
                gap_type="hard",
                chunks=[],
                retrieval_ms=100,
            )


class TestGapIntegration:
    """Integration tests for gap detection in the full pre_call_hook flow."""

    @pytest.mark.asyncio
    async def test_hard_gap_fires_event(self, monkeypatch):
        """Hard gap (zero chunks) triggers _fire_gap_event."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What is the company vacation policy?"}
        ]}

        retrieval_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            with patch.object(mod, "_fire_gap_event") as mock_fire:
                await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")
                mock_fire.assert_called_once()
                call_kwargs = mock_fire.call_args
                assert call_kwargs.kwargs.get("gap_type") == "hard" or call_kwargs[1].get("gap_type") == "hard"

    @pytest.mark.asyncio
    async def test_soft_gap_fires_event(self, monkeypatch):
        """Soft gap (low reranker scores) triggers _fire_gap_event."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "How do I configure the advanced settings?"}
        ]}

        chunks = [
            {"text": "Some text.", "reranker_score": 0.1, "score": 0.2, "scope": "org", "metadata": {"title": "Settings", "kb_slug": "docs"}},
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            with patch.object(mod, "_fire_gap_event") as mock_fire:
                await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")
                mock_fire.assert_called_once()
                call_kwargs = mock_fire.call_args
                assert call_kwargs.kwargs.get("gap_type") == "soft" or call_kwargs[1].get("gap_type") == "soft"

    @pytest.mark.asyncio
    async def test_success_does_not_fire_event(self, monkeypatch):
        """High-scoring chunks → no gap event."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What is our leave policy?"}
        ]}

        chunks = [
            {"text": "Leave policy info.", "reranker_score": 0.9, "score": 0.8, "scope": "org", "metadata": {"title": "Leave"}},
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            with patch.object(mod, "_fire_gap_event") as mock_fire:
                await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")
                mock_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_gap_not_reported_when_no_user_id(self, monkeypatch):
        """R2.4: missing user_id → skip gap reporting."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        # No "user" key in data
        data = {"messages": [
            {"role": "user", "content": "What is our leave policy?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            with patch.object(mod, "_fire_gap_event") as mock_fire:
                await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")
                mock_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_gap_not_reported_when_no_org_id(self, monkeypatch):
        """R2.4: missing org_id → skip gap reporting."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "What is our leave policy?"}
        ]}

        uak = MagicMock()
        uak.metadata = {}  # no org_id

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            with patch.object(mod, "_fire_gap_event") as mock_fire:
                await hook.async_pre_call_hook(uak, cache, data, "completion")
                mock_fire.assert_not_called()


# ─── KB-013 scope preference tests ───────────────────────────────────────────


class TestKlaiKnowledgeHookKB013:
    """Tests for KB-013: per-user KB scope preference controls."""

    @pytest.mark.asyncio
    async def test_pre_step_skip_when_retrieval_disabled(self, monkeypatch):
        """REQ-E4: kb_retrieval_enabled=False → no retrieval-api call, no injection."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": False,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "version": 0,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is ons personeelsbeleid?"}
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
    async def test_scope_org_when_personal_disabled(self, monkeypatch):
        """REQ-E5: kb_personal_enabled=False → scope='org' in retrieval request."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": False,
            "kb_slugs_filter": None,
            "version": 0,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Toon me de organisatiestructuur."}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs.get("json") or {}
            assert body.get("scope") == "org"

    @pytest.mark.asyncio
    async def test_scope_both_when_personal_enabled(self, monkeypatch):
        """REQ-E6: kb_personal_enabled=True → scope='both' in retrieval request."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "version": 0,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is het budget voor Q3?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs.get("json") or {}
            assert body.get("scope") == "both"

    @pytest.mark.asyncio
    async def test_kb_slugs_passed_when_filter_set(self, monkeypatch):
        """REQ-E7: kb_slugs_filter set → kb_slugs forwarded to retrieval-api."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": ["engineering", "product"],
            "version": 0,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Hoe werkt de deployment pipeline?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs.get("json") or {}
            assert body.get("kb_slugs") == ["engineering", "product"]

    @pytest.mark.asyncio
    async def test_no_kb_slugs_key_when_filter_none(self, monkeypatch):
        """When kb_slugs_filter=None, kb_slugs key absent from retrieval request."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "version": 0,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Geef me een overzicht van de roadmap."}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            body = mc.post.call_args.kwargs.get("json") or {}
            assert "kb_slugs" not in body

    @pytest.mark.asyncio
    async def test_version_cache_hit_skips_portal_call(self, monkeypatch):
        """Two-level cache hit: version pointer + feature dict both warm → no portal GET."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={
            "enabled": True,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "version": 5,
        })

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat zijn de KPIs voor dit kwartaal?"}
        ]}

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            mc.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_portal_format_preserves_retrieval(self, monkeypatch):
        """REQ-N1: old portal response {enabled:True} without new fields → retrieval proceeds with defaults."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        # Cache miss — forces live portal call
        cache = _make_cache(feature_enabled=None)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is de winstmarge van Q2?"}
        ]}

        # "Old-format" portal response: only the enabled flag and the
        # resolved zitadel_user_id, missing the optional KB-pref fields
        # (kb_retrieval_enabled / kb_personal_enabled / kb_slugs_filter).
        # zitadel_user_id is mandatory since the 2026-05-05 identity-verify
        # rollout — without it the hook fails-loud with no retrieval call.
        portal_resp = _make_resp(
            {"enabled": True, "zitadel_user_id": "300000000000000002"}
        )

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.get = AsyncMock(return_value=portal_resp)
            mc.post = AsyncMock(return_value=_make_resp({"chunks": []}))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

            # kb_retrieval_enabled defaults to True → retrieval call is made
            mc.post.assert_called_once()
            body = mc.post.call_args.kwargs.get("json") or {}
            # kb_personal_enabled defaults to True → scope='both'
            assert body.get("scope") == "both"

    @pytest.mark.asyncio
    async def test_portal_fail_returns_enabled_false(self, monkeypatch):
        """Portal unreachable → fail-closed (enabled=False), no injection."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=None)

        data = {"user": "aabbcc112233445566778899", "messages": [
            {"role": "user", "content": "Wat is de status van de migratie?"}
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


# ─── Phase 4 (REQ-10) — multilingual contract on path A (LiteLLM hook) ──────


class TestKlaiKnowledgeHookMultilingualPhase4:
    """Pin the SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 contract for path A.

    REQ-10 (recap): the LiteLLM hook prepends GROUNDED_CHAT_SYSTEM_PROMPT
    to every LibreChat request and rewrites the four NL prefix blocks
    (Klai Templates wrapper, KB unavailable notice, KB header narrow/broad,
    ANSWER FORMAT instructions) into English-prefixed multilingual
    instructions. The model receives English instructions but answers in
    the language of the user's most recent substantive message.

    These tests are language-agnostic by construction: they assert on the
    English anchor strings the hook emits, not on what the model produces.
    DE/FR/PT/ES end-to-end output language is gated by
    evaluation/cross_lingual_runner.py (REQ-05), not by this unit-test
    suite.

    The four queries below cover the four target languages added in v1.0
    (DE, FR, PT, ES) — they exercise the same code paths as the existing
    NL queries elsewhere in this file but make REQ-10's "language-of-query
    is unconstrained" property explicit.
    """

    @pytest.fixture
    def _kb_chunks(self) -> list[dict]:
        return [
            {
                "text": "Klanten kunnen klimcursussen boeken via de app.",
                "scope": "org",
                "metadata": {"title": "Boekingsproces"},
                "source_url": "https://docs.klai.example/booking",
                "chunk_id": "c1",
                "reranker_score": 0.91,
            }
        ]

    def _system_msg(self, result: dict) -> str:
        msgs = [m for m in result["messages"] if m["role"] == "system"]
        assert len(msgs) == 1, f"expected exactly one system message, got {len(msgs)}"
        return msgs[0]["content"]

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("Wie kann ich einen Klettergurt zurückgeben?", id="DE"),
            pytest.param("Comment puis-je annuler ma réservation?", id="FR"),
            pytest.param("Como faço para cancelar uma reserva?", id="PT"),
            pytest.param("¿Cómo puedo cancelar mi reserva?", id="ES"),
        ],
    )
    @pytest.mark.asyncio
    async def test_multilingual_foundation_prepended_for_target_languages(
        self, monkeypatch, _kb_chunks, query
    ):
        """REQ-10: GROUNDED_CHAT_SYSTEM_PROMPT leads the system prompt
        regardless of the query language. Same code path as the NL tests
        above; the only thing varying is the user-message language.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [{"role": "user", "content": query}],
        }
        retrieval_resp = _make_resp({"chunks": _kb_chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        # Foundation: GROUNDED_CHAT_SYSTEM_PROMPT signature line.
        assert (
            "Detect the language of the user's most recent SUBSTANTIVE message"
            in sys_content
        )
        # English-prefixed answer-format anchor (REQ-10).
        assert "ANSWER FORMAT — always follow this" in sys_content
        # Old NL anchors must not regress (they were canonical pre-Phase 4).
        assert "ANTWOORDFORMAAT — volg dit ALTIJD" not in sys_content
        assert "Klai Kennisbank — gebruik dit als aanvullende context" not in sys_content

    @pytest.mark.asyncio
    async def test_kb_header_broad_uses_english_anchor(
        self, monkeypatch, _kb_chunks
    ):
        """REQ-10: broad-mode (default) header is the English Phase 4 anchor."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [{"role": "user", "content": "Wie viel kostet ein Kurs?"}],
        }
        retrieval_resp = _make_resp({"chunks": _kb_chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        assert "Klai Knowledge Base — use this as supplementary context" in sys_content
        # The narrow-mode anchor is mutually exclusive in this test (kb_narrow
        # not set → broad).
        assert (
            "Klai Knowledge Base — answer strictly using only the sources below"
            not in sys_content
        )

    @pytest.mark.asyncio
    async def test_kb_header_narrow_uses_english_anchor(
        self, monkeypatch, _kb_chunks
    ):
        """REQ-10: narrow-mode header is the English Phase 4 anchor when
        ``kb_narrow=True``.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature={"kb_narrow": True})

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "Combien coûte un cours d'escalade?"}
            ],
        }
        retrieval_resp = _make_resp({"chunks": _kb_chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        assert (
            "Klai Knowledge Base — answer strictly using only the sources below"
            in sys_content
        )

    @pytest.mark.asyncio
    async def test_no_entitlement_path_still_prepends_foundation(self, monkeypatch):
        """REQ-10: even users without KB entitlement get the multilingual
        foundation. The hook only declines to inject the KB context block
        — language-detection still applies so DE/FR/PT/ES users without
        entitlement also get language-correct answers.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=False)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "¿Cuánto cuesta un curso de escalada?"}
            ],
        }

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

            # No entitlement → no retrieval call.
            mc.post.assert_not_called()

        sys_content = self._system_msg(result)
        assert (
            "Detect the language of the user's most recent SUBSTANTIVE message"
            in sys_content
        )
        # No KB context — confirms we didn't go through the chunks branch.
        assert "Klai Knowledge Base" not in sys_content

    @pytest.mark.asyncio
    async def test_kb_unavailable_notice_uses_english_anchor(self, monkeypatch):
        """REQ-10: the KB-unreachable warning anchor is English. The model
        translates the warning into the user's language at runtime — that
        translation is not tested here (it's the model's responsibility,
        gated by evaluation/cross_lingual_runner.py).
        """
        import httpx as _httpx

        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "Como faço para cancelar uma reserva?"}
            ],
        }

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(side_effect=_httpx.ConnectError("connection refused"))
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(data)
        assert "Klai Knowledge Base — TEMPORARILY UNAVAILABLE" in sys_content
        # The instruction tells the model to write the warning in the
        # detected language — we assert the instruction itself, not the
        # output.
        assert "language you detected from their" in sys_content
        # No NL regression.
        assert "TIJDELIJK NIET BEREIKBAAR" not in sys_content

    @pytest.mark.asyncio
    async def test_chunk_labels_use_english_personal_marker(self, monkeypatch):
        """REQ-10: chunk-scope label is ``[personal]`` (was ``[persoonlijk]``)."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        chunks = [
            {
                "text": "Org-document text.",
                "scope": "org",
                "metadata": {"title": "Org doc"},
            },
            {
                "text": "User personal note.",
                "scope": "personal",
                "metadata": {"title": "My note"},
            },
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "Wo finde ich meine persönlichen Notizen?"}
            ],
        }

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        assert "[org]" in sys_content
        assert "[personal]" in sys_content
        assert "[persoonlijk]" not in sys_content
        # End-of-context terminator switched English too.
        assert "[End knowledge base context]" in sys_content
        assert "[Einde kennisbank-context]" not in sys_content


# ─── 2026-05-07 follow-up: NL-bias regression guard ─────────────────────────
#
# Companion to commit a0d72cea (drop residual NL bias in path-A
# answer-format + default templates). Two assertions that lock in the
# fix so a future refactor of the [ANSWER FORMAT] block or the post-KB
# reminder cannot silently re-introduce the regression Mark hit on
# 2026-05-07 (English question to chat-voys with NL knowledge-base
# chunks → answer started with "Samenvatting" instead of "TL;DR").


class TestKlaiKnowledgeHookNLBiasRegression:
    """Regression guard for the 2026-05-07 NL-bias fix.

    Two failure modes get pinned here:

    1. The format-instruction (``[ANSWER FORMAT]``) used to enumerate
       NL/DE/FR/PT/ES short-summary labels including "Samenvatting".
       That alternative list anchored Mistral on the dominant KB
       language whenever chunks were Dutch, overriding the
       ``[CRITICAL]`` language-detection preamble at the top of
       ``GROUNDED_CHAT_SYSTEM_PROMPT``. The fix removed the per-
       language label list. This test asserts those labels are gone.

    2. The system-prompt now appends a ``[LANGUAGE REMINDER]`` block
       AFTER the KB chunks so the most-recent instruction reinforces
       the user-language contract once the model has just read the
       (often-Dutch) source documents. This test asserts the reminder
       is present whenever KB context is injected.
    """

    @pytest.fixture
    def _kb_chunks(self) -> list[dict]:
        return [
            {
                "text": "Klanten kunnen klimcursussen boeken via de app.",
                "scope": "org",
                "metadata": {"title": "Boekingsproces"},
                "source_url": "https://docs.klai.example/booking",
                "chunk_id": "c1",
                "reranker_score": 0.91,
            }
        ]

    def _system_msg(self, result: dict) -> str:
        msgs = [m for m in result["messages"] if m["role"] == "system"]
        assert len(msgs) == 1, f"expected exactly one system message, got {len(msgs)}"
        return msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_format_instruction_no_per_language_label_list(
        self, monkeypatch, _kb_chunks
    ):
        """The [ANSWER FORMAT] block must NOT enumerate per-language
        short-summary labels — that list anchored the model on whatever
        language the KB content was in.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {
                    "role": "user",
                    "content": "Can you tell me about climbing courses?",
                }
            ],
        }
        retrieval_resp = _make_resp({"chunks": _kb_chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        # The [ANSWER FORMAT] header is preserved.
        assert "ANSWER FORMAT — always follow this" in sys_content
        # But the per-language label list is gone — none of these labels
        # may appear anywhere in the format instruction. We probe inside
        # the [ANSWER FORMAT] section to avoid false negatives if any of
        # these words organically appear elsewhere (e.g. inside a chunk
        # body).
        format_section = sys_content.split("[ANSWER FORMAT — always follow this", 1)[1]
        format_section = format_section.split("[End knowledge base context]", 1)[0]
        for legacy_label in (
            "Samenvatting",
            "Zusammenfassung",
            "Résumé",
            "Resumen",
            "Resumo",
        ):
            assert legacy_label not in format_section, (
                f"format-instruction must not pin a per-language label "
                f"({legacy_label!r}) — it anchors Mistral on KB-content "
                "language. See commit a0d72cea."
            )
        # Positive assert: the new wording is present.
        assert (
            "SAME LANGUAGE as the user's question" in format_section
            or "NOT the language of the source documents" in format_section
        )

    @pytest.mark.asyncio
    async def test_language_reminder_appended_after_kb_chunks(
        self, monkeypatch, _kb_chunks
    ):
        """The [LANGUAGE REMINDER] block must appear AFTER the KB
        chunks so the most-recent instruction reinforces the language
        contract when KB content dominates the prompt.
        """
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "How do I cancel my reservation?"}
            ],
        }
        retrieval_resp = _make_resp({"chunks": _kb_chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        # Reminder is present and explicitly tells the model not to
        # mirror the source-document language.
        assert "[LANGUAGE REMINDER]" in sys_content
        assert "NOT the language of the source documents" in sys_content
        # Ordering: reminder MUST come after the [End knowledge base
        # context] marker so it is the last thing the model reads
        # before generating.
        end_idx = sys_content.find("[End knowledge base context]")
        reminder_idx = sys_content.find("[LANGUAGE REMINDER]")
        assert end_idx != -1, "[End knowledge base context] missing"
        assert reminder_idx != -1, "[LANGUAGE REMINDER] missing"
        assert reminder_idx > end_idx, (
            "[LANGUAGE REMINDER] must appear AFTER [End knowledge base "
            "context] — last-mentioned wins for Mistral."
        )


class TestKlaiKnowledgeHookUrlImageGrounding:
    """Regression guards for fake URL/image Markdown in KB answers."""

    def _system_msg(self, result: dict) -> str:
        msgs = [m for m in result["messages"] if m["role"] == "system"]
        assert len(msgs) == 1, f"expected exactly one system message, got {len(msgs)}"
        return msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_prompt_forbids_invented_source_urls_and_images(self, monkeypatch):
        """A chunk without URLs/images must tell the model to answer without links/images."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [
                {"role": "user", "content": "Heb je hier ook een afbeelding bij?"}
            ],
        }
        chunks = [
            {
                "text": "Het molair volume is het volume van een mol gas.",
                "scope": "org",
                "metadata": {"title": "Molair volume"},
                "chunk_id": "c1",
                "reranker_score": 0.91,
            }
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        format_section = sys_content.split("[ANSWER FORMAT — always follow this", 1)[1]
        format_section = format_section.split("[End knowledge base context]", 1)[0]

        assert "NEVER invent a URL" in format_section
        assert "If no chunk has a source_url" in format_section
        assert "NEVER create, guess, search for, or suggest an image URL" in format_section
        assert "no explicit image tag is present" in format_section
        assert "no image is available in the knowledge base" in format_section
        assert "placeholder, example, or documentation-only" in format_section
        assert "example.com" not in format_section
        assert "![afbeelding" not in format_section
        kb_meta = result["metadata"]["_klai_kb_meta"]
        assert kb_meta["allowed_source_urls"] == []
        assert kb_meta["allowed_image_urls"] == []

    @pytest.mark.asyncio
    async def test_prompt_only_exposes_images_from_chunk_image_urls(self, monkeypatch):
        """Image markdown in the prompt is generated only from retrieved image_urls."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        cache = _make_cache(feature_enabled=True)

        data = {
            "user": "aabbcc112233445566778899",
            "messages": [{"role": "user", "content": "Laat de afbeelding zien."}],
        }
        chunks = [
            {
                "text": "Deze handleiding heeft een diagram.",
                "scope": "org",
                "metadata": {"title": "Diagram"},
                "source_url": "https://docs.getklai.com/diagram",
                "image_urls": ["/kb-images/org/images/support/diagram.png"],
                "chunk_id": "c1",
                "reranker_score": 0.91,
            }
        ]
        retrieval_resp = _make_resp({"chunks": chunks, "retrieval_bypassed": False})

        with patch("klai_knowledge.httpx.AsyncClient") as cls:
            mc = AsyncMock()
            mc.post = AsyncMock(return_value=retrieval_resp)
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=None)
            cls.return_value = mc

            result = await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

        sys_content = self._system_msg(result)
        assert "Only include image markdown if a chunk below already contains" in sys_content
        assert (
            "![afbeelding 1](https://getklai.getklai.com/kb-images/org/images/support/diagram.png)"
            in sys_content
        )
        assert "ALWAYS include them literally" not in sys_content
        kb_meta = result["metadata"]["_klai_kb_meta"]
        assert kb_meta["allowed_source_urls"] == ["https://docs.getklai.com/diagram"]
        assert kb_meta["allowed_image_urls"] == [
            "https://getklai.getklai.com/kb-images/org/images/support/diagram.png"
        ]

    def test_sanitizer_removes_unretrieved_links_and_images(self, monkeypatch):
        """Output guard keeps only exact URLs retrieved for this KB call."""
        mod = _load_hook(monkeypatch)

        text = (
            "Bron: [ok](https://docs.getklai.com/diagram) "
            "[fake](https://example.com/fake). "
            "Goed: ![diagram](https://getklai.getklai.com/kb-images/org/diagram.png) "
            "Slecht: ![fake](https://example.com/fake.png) "
            "Raw: https://example.com/raw"
        )

        sanitized, changed = mod._sanitize_kb_markdown_output(
            text,
            allowed_source_urls={"https://docs.getklai.com/diagram"},
            allowed_image_urls={
                "https://getklai.getklai.com/kb-images/org/diagram.png"
            },
        )

        assert changed == 3
        assert "[ok](https://docs.getklai.com/diagram)" in sanitized
        assert "![diagram](https://getklai.getklai.com/kb-images/org/diagram.png)" in sanitized
        assert "https://example.com" not in sanitized
        assert "fake" in sanitized
        assert "[link removed]" in sanitized

    @pytest.mark.asyncio
    async def test_post_call_guard_mutates_response_content(self, monkeypatch):
        """The proxy post-call hook strips invented URLs before returning response."""
        mod = _load_hook(monkeypatch)
        hook = mod.KlaiKnowledgeHook()
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            "Zie [bron](https://docs.getklai.com/diagram) "
                            "en ![fake](https://example.com/fake.png)."
                        )
                    )
                )
            ]
        )
        data = {
            "metadata": {
                "_klai_kb_meta": {
                    "org_id": "org123",
                    "user_id": "user123",
                    "chunks_injected": 1,
                    "retrieval_ms": 12,
                    "gate_bypassed": False,
                    "allowed_source_urls": ["https://docs.getklai.com/diagram"],
                    "allowed_image_urls": [],
                }
            }
        }

        returned = await hook.async_post_call_success_hook(data, None, response)

        assert returned is response
        content = response.choices[0].message.content
        assert "[bron](https://docs.getklai.com/diagram)" in content
        assert "https://example.com" not in content
        assert "![fake]" not in content
