"""Tests for the meta-query early-return path added 2026-05-12.

Background: SPEC-CHAT-META-QUERY-001 (Voys "Meldingen" incident retro).

A vague meta-question — "wat kan ik hier?" — fell through `_is_trivial`
(16 chars > 8-byte threshold, no pattern match), reached retrieval, and
matched a tangential Voys KB chunk about voice-mail announcements on
common Dutch lexical overlap. The Klai chat presented that chunk as
the answer, then defended it when the user asked why ("dat staat in
de kennisbank"). No mechanism existed to detect "this is a question
ABOUT Klai, not a content question" before retrieval ran.

The fix adds three things to deploy/litellm/klai_knowledge.py:

1. ``_META_QUERY_PATTERNS`` — a tight anchored regex.
2. ``_is_meta_query`` — predicate function.
3. An early-return path in ``async_pre_call_hook`` that injects
   ``META_CHAT_SYSTEM_PROMPT`` and skips retrieval entirely.

These tests guard those three points:
- Regex matches every documented meta phrasing (NL + EN positives).
- Regex does NOT match content questions that contain meta-y substrings
  (the critical failure mode — false positives would silently strip KB
  retrieval from legitimate user questions).
- The hook's early-return path actually fires: META prompt is prepended,
  retrieval HTTP call is never made, KB feature lookup is never made.
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


# ─── Mock litellm so klai_knowledge can be imported in the test env ────


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Same shim as test_klai_knowledge_hook.py — litellm is not installed
    locally (lives only inside the Docker image). Build a minimal stub that
    satisfies the ``from litellm.integrations.custom_logger import
    CustomLogger`` import path in klai_knowledge.
    """
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

    for mod_name in (
        "litellm",
        "litellm.integrations",
        "litellm.integrations.custom_logger",
    ):
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _load_hook(monkeypatch):
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_klai_kb_modules()
    import klai_knowledge

    importlib.reload(klai_knowledge)
    # Silence fire-and-forget producers per .claude/rules/klai/lang/testing.md
    # (coroutine-never-awaited at GC). Not strictly needed for the meta-path
    # since retrieval is skipped, but mirrors the canonical pattern from
    # test_klai_knowledge_hook.py.
    monkeypatch.setattr(klai_knowledge, "_fire_gap_event", MagicMock())
    monkeypatch.setattr(klai_knowledge, "_fire_retrieval_log", MagicMock())
    return klai_knowledge


# ─── Regex positives — phrasings that MUST match ───────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        # ─── Dutch ────────────────────────────────────────────────
        "wat kan ik hier?",
        "wat kan ik hier",
        "Wat kan ik hier doen?",
        "wat kan ik met klai",
        "wat kan ik met jou",
        "wat kan ik met je doen?",
        "wat kan ik allemaal?",
        "wat is klai?",
        "wat doet klai",
        "wat doe je",
        "wat kan je",
        "Wat kan je doen?",
        "wat kun je?",
        "wat kan klai doen?",
        "hoe werkt dit?",
        "hoe werkt klai",
        "hoe werkt deze chat",
        "hoe gebruik ik klai?",
        "hoe gebruik ik deze chat",
        "wie ben je?",
        "waarvoor is dit?",
        "waar is dit voor?",
        "help",
        "Help?",
        # ─── English ──────────────────────────────────────────────
        "what can I do?",
        "what can I do here?",
        "What can I do with Klai?",
        "what can I do with you",
        "what can you do?",
        "what can you do here",
        "what is klai?",
        "what is Klai",
        "what does klai do",
        "what are klai",
        "how does this work?",
        "how does klai work",
        "how does this chat work?",
        "how do I use klai?",
        "how do I use this chat?",
        "who are you?",
        "Help",
    ],
)
def test_meta_query_matches_known_phrasings(phrase, monkeypatch):
    mod = _load_hook(monkeypatch)
    assert mod._is_meta_query(phrase), f"expected meta-match: {phrase!r}"


# ─── Regex negatives — content questions that MUST NOT match ───────────
#
# These are the high-cost false-positive cases. A user asking a real
# content question that lexically contains "wat kan ik" or "how does"
# must NOT be misclassified as a meta-question — that would silently
# strip retrieval and produce a non-answer.


@pytest.mark.parametrize(
    "phrase",
    [
        # NL — content questions that lexically contain meta substrings
        "wat kan ik doen om mijn factuur te betalen?",
        "wat kan ik aanpassen aan onze prijsstrategie",
        "wat is onze hoofdkantoor in Amsterdam",
        "wat doet onze HR-afdeling met vakantiedagen",
        "hoe werkt onze prijsstrategie?",
        "hoe werkt de salesfunnel bij ons",
        "hoe gebruik ik het CRM",
        "ik wil weten wat ik hier kan",
        "ik wil weten hoe dit werkt voor klanten",
        "kun je me helpen met het onboarding-proces?",
        "help me met deze klant",
        # EN — content questions with meta substrings
        "what can I do to escalate a customer issue?",
        "what does our refund policy say",
        "what is our pricing for enterprise?",
        "how does our pricing tier work?",
        "how does this customer escalation work",
        "how do I use the CRM",
        "i need to know what can I do about a refund",
        "help me with the onboarding flow",
        # Edge: long messages where meta phrase is a substring
        "Klant vraagt wat kan ik hier doen, hoe leg ik dat uit?",
        "How does this product compare to competitor X for enterprise?",
    ],
)
def test_meta_query_does_not_match_content_questions(phrase, monkeypatch):
    mod = _load_hook(monkeypatch)
    assert not mod._is_meta_query(phrase), (
        f"false-positive meta-match for content question: {phrase!r}"
    )


# ─── Integration: hook early-return injects META and skips retrieval ───


def _make_cache():
    """Minimal cache that returns empty templates and forces a kb_feature
    cache miss. The meta-path returns BEFORE kb_feature is consulted, so
    the cache miss should never trigger an HTTP call — that's the
    invariant the integration test guards.
    """
    cache = MagicMock()
    cache.async_set_cache = AsyncMock()

    async def _get(key: str) -> object:
        if key.startswith("templates:"):
            return []
        return None

    cache.async_get_cache = AsyncMock(side_effect=_get)
    return cache


def _make_user_api_key(org_id: str = "org123"):
    uak = MagicMock()
    uak.metadata = {"org_id": org_id}
    return uak


@pytest.mark.asyncio
async def test_meta_query_injects_meta_prompt_and_skips_retrieval(monkeypatch):
    """End-to-end: 'wat kan ik hier?' triggers the early-return path.

    Asserts:
      1. ``META_CHAT_SYSTEM_PROMPT`` is prepended to the system message.
      2. ``GROUNDED_CHAT_SYSTEM_PROMPT`` is NOT present (would mean the
         hook fell through to the libre-chat path).
      3. No httpx call is made (no retrieval, no kb_feature lookup) —
         this is the cost-saving + correctness invariant: retrieval
         must not run for meta-questions.
    """
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache()

    data = {
        "user": "aabbcc112233445566778899",
        "messages": [{"role": "user", "content": "wat kan ik hier?"}],
    }

    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.get = AsyncMock()
        mc.post = AsyncMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        result = await hook.async_pre_call_hook(
            _make_user_api_key(), cache, data, "completion"
        )

        # Invariant 3: no HTTP calls at all on the meta path.
        assert mc.post.await_count == 0, (
            "meta-query path must not call retrieval-api /retrieve"
        )
        assert mc.get.await_count == 0, (
            "meta-query path must not call portal-api /internal/kb-feature"
        )

    messages = result["messages"]
    # Invariant 1: there's now a system message and it starts with META.
    assert messages[0]["role"] == "system"
    sys_content = messages[0]["content"]
    # Anchor on a unique META section marker — the language preamble is
    # shared with GROUNDED/GENERAL so we cannot match on the [CRITICAL]
    # prefix alone.
    assert "META question about Klai itself" in sys_content, (
        "system message does not contain META_CHAT_SYSTEM_PROMPT body"
    )
    # Invariant 2: the GROUNDED-only KB header is absent.
    assert "knowledge base chunks provided" not in sys_content, (
        "GROUNDED prompt leaked into the meta-question path"
    )


@pytest.mark.asyncio
async def test_meta_query_is_not_triggered_by_content_question(monkeypatch):
    """A content question that lexically contains 'hoe werkt' must take the
    normal retrieval path (HTTP call to /retrieve), not the META early
    return. Guards the regex's false-positive rate at the hook level.
    """
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()

    # Use the real kb_feature cache shape so the hook reaches retrieval.
    cache = MagicMock()
    cache.async_set_cache = AsyncMock()
    feat = {
        "enabled": True,
        "kb_retrieval_enabled": True,
        "kb_personal_enabled": True,
        "kb_slugs_filter": [],
        "version": 0,
        "zitadel_user_id": "300000000000000002",
    }

    async def _get(key: str) -> object:
        if key.startswith("kb_ver:"):
            return "0"
        if key.startswith("kb_feature:"):
            return feat
        if key.startswith("templates:"):
            return []
        if key.startswith("tax_trees:") or key.startswith("tax_coverage:"):
            return {}
        return None

    cache.async_get_cache = AsyncMock(side_effect=_get)

    data = {
        "user": "aabbcc112233445566778899",
        "messages": [{"role": "user", "content": "hoe werkt onze prijsstrategie?"}],
    }

    feature_resp = MagicMock()
    feature_resp.status_code = 200
    feature_resp.json.return_value = {
        "enabled": True,
        "kb_retrieval_enabled": True,
        "kb_personal_enabled": True,
        "kb_slugs_filter": [],
        "kb_narrow": False,
        "kb_pref_version": 0,
        "zitadel_user_id": "300000000000000002",
    }
    feature_resp.raise_for_status = MagicMock()

    retrieval_resp = MagicMock()
    retrieval_resp.status_code = 200
    retrieval_resp.json.return_value = {"chunks": [], "retrieval_bypassed": False}
    retrieval_resp.raise_for_status = MagicMock()

    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.get = AsyncMock(return_value=feature_resp)
        mc.post = AsyncMock(return_value=retrieval_resp)
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        await hook.async_pre_call_hook(_make_user_api_key(), cache, data, "completion")

        # Content questions MUST reach retrieval. Without this assertion
        # an over-eager regex tweak would silently strip KB access from
        # every "how does our X work" style question.
        assert mc.post.await_count >= 1, (
            "content question with 'hoe werkt' substring must still call "
            "retrieval — the meta regex must NOT match it"
        )
