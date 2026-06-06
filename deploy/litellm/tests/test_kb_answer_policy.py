"""Contract tests for KbAnswerPolicy — the single source of truth for the
``_klai_kb_meta`` shape and the prompt/post-call answer-policy flags.

These lock the maintainability guarantee introduced when the 5 hand-copied
``_klai_kb_meta`` dicts in ``async_pre_call_hook`` were collapsed into
``KbAnswerPolicy.to_kb_meta``: every pre-call branch now emits the SAME key
set, so a new branch (or a renamed key) can't silently diverge.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _mock_litellm(monkeypatch):
    """Stub litellm + minimal env so klai_knowledge imports outside the container."""
    monkeypatch.setenv("KNOWLEDGE_RETRIEVE_URL", "http://retrieval-api:8040/retrieve")
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "test-portal-secret")
    monkeypatch.setenv("RETRIEVAL_INTERNAL_SECRET", "test-retrieval-secret")
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

    for mod_name in ("litellm", "litellm.integrations", "litellm.integrations.custom_logger"):
        sys.modules.pop(mod_name, None)
    sys.modules.pop("klai_knowledge", None)


def _kk():
    import klai_knowledge as kk

    return kk


# Every state the pre-call hook can return on.
_ALL_STATES = [
    "retrieval_failure",
    "gate_bypassed",
    "missing_evidence_pack",
    "zero_chunks",
    "chunks_present",
]

# The complete contract: the exact key set to_kb_meta must always produce.
_EXPECTED_KEYS = {
    "org_id",
    "user_id",
    "user_query",
    "kb_narrow",
    "chunks_injected",
    "chunk_ids",
    "allowed_source_urls",
    "allowed_image_urls",
    "citation_source_urls",
    "citation_chunks",
    "trusted_sources",
    "evidence_pack",
    "citable_sources_count",
    "confidence_band",
    "no_citable_sources",
    "no_citable_reason",
    "no_citable_message",
    "original_stream",
    "render_mode",
    "retrieval_ms",
    "gate_bypassed",
    "retrieval_failure",
    # policy-derived
    "answer_policy_state",
    "answer_policy_mode",
    "user_provided_content_context",
    "low_confidence_inject",
    "allow_uncited_user_content",
    "suppress_kb_citations",
}


def _policy(state: str, **kw):
    return _kk().KbAnswerPolicy(
        state=state,
        kb_narrow=kw.get("kb_narrow", False),
        user_provided_content_context=kw.get("upc", False),
        low_confidence_inject=kw.get("low_conf", False),
    )


def test_to_kb_meta_key_set_is_identical_across_every_state():
    """No branch may drop or add a key — the shape is one contract."""
    for state in _ALL_STATES:
        meta = _policy(state).to_kb_meta(org_id="o", user_id="u", retrieval_ms=1)
        assert set(meta) == _EXPECTED_KEYS, f"state={state} diverged: {set(meta) ^ _EXPECTED_KEYS}"


def test_to_kb_meta_minimal_call_defaults_are_safe():
    """A branch that knows almost nothing still gets a fully-formed dict."""
    meta = _policy("gate_bypassed").to_kb_meta(org_id="o", user_id="u", retrieval_ms=5)
    assert meta["chunk_ids"] == []
    assert meta["citation_source_urls"] == {}
    assert meta["trusted_sources"] == []
    assert meta["evidence_pack"] is None
    assert meta["no_citable_sources"] is False
    assert meta["no_citable_reason"] is None
    assert meta["retrieval_failure"] is False
    assert meta["confidence_band"] is None


def test_mode_follows_kb_narrow():
    assert _policy("chunks_present", kb_narrow=True).mode == "strict"
    assert _policy("chunks_present", kb_narrow=False).mode == "open"


def test_allow_uncited_user_content_tracks_user_provided_context():
    assert _policy("zero_chunks", upc=True).allow_uncited_user_content is True
    assert _policy("zero_chunks", upc=False).allow_uncited_user_content is False


def test_suppress_kb_citations_only_on_low_conf_chunk_states():
    # Requires BOTH user content AND low-confidence injection AND a chunk state.
    assert _policy("chunks_present", upc=True, low_conf=True).suppress_kb_citations is True
    assert _policy("zero_chunks", upc=True, low_conf=True).suppress_kb_citations is True
    # Not on non-chunk states even with the same flags.
    assert _policy("retrieval_failure", upc=True, low_conf=True).suppress_kb_citations is False
    assert _policy("gate_bypassed", upc=True, low_conf=True).suppress_kb_citations is False
    assert _policy("missing_evidence_pack", upc=True, low_conf=True).suppress_kb_citations is False
    # Needs both flags.
    assert _policy("chunks_present", upc=True, low_conf=False).suppress_kb_citations is False
    assert _policy("chunks_present", upc=False, low_conf=True).suppress_kb_citations is False


def test_to_kb_meta_propagates_policy_flags_into_metadata():
    meta = _policy("chunks_present", kb_narrow=True, upc=True, low_conf=True).to_kb_meta(
        org_id="o", user_id="u", retrieval_ms=1
    )
    assert meta["answer_policy_state"] == "chunks_present"
    assert meta["answer_policy_mode"] == "strict"
    assert meta["kb_narrow"] is True
    assert meta["user_provided_content_context"] is True
    assert meta["low_confidence_inject"] is True
    assert meta["allow_uncited_user_content"] is True
    assert meta["suppress_kb_citations"] is True
