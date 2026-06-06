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

from tests.klai_module_reset import reset_klai_kb_modules


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

    for mod_name in (
        "litellm",
        "litellm.integrations",
        "litellm.integrations.custom_logger",
    ):
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _kk():
    import klai_knowledge as kk

    return kk


def _policy_module():
    import klai_kb_answer_policy as policy

    return policy


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
    "kb_scope_mode",
    "kbs_in_scope",
    "kbs_with_results",
    "kbs_used_as_sources",
    # policy-derived
    "answer_policy_state",
    "chat_retrieval_prompt_mode",
    "answer_policy_mode",
    "user_provided_content_context",
    "low_confidence_inject",
    "allow_uncited_user_content",
    "suppress_kb_citations",
}


def _policy(state: str, **kw):
    return _kk().KbAnswerPolicy(
        state=state,
        prompt_mode=kw.get("prompt_mode", "open_kb"),
        user_provided_content_context=kw.get("upc", False),
        low_confidence_inject=kw.get("low_conf", False),
    )


def test_to_kb_meta_key_set_is_identical_across_every_state():
    """No branch may drop or add a key — the shape is one contract."""
    for state in _ALL_STATES:
        meta = _policy(state).to_kb_meta(org_id="o", user_id="u", retrieval_ms=1)
        assert set(meta) == _EXPECTED_KEYS, (
            f"state={state} diverged: {set(meta) ^ _EXPECTED_KEYS}"
        )


def test_policy_module_declares_every_pre_call_answer_state():
    assert tuple(_ALL_STATES) == _policy_module().KB_ANSWER_POLICY_STATES


def test_answer_policy_matrix_is_independent_of_renderer_state():
    policy_module = _policy_module()
    for state in policy_module.KB_ANSWER_POLICY_STATES:
        for prompt_mode in ("open_kb", "strict_kb"):
            for user_content in (False, True):
                for low_confidence in (False, True):
                    policy = policy_module.KbAnswerPolicy(
                        state=state,
                        prompt_mode=prompt_mode,
                        user_provided_content_context=user_content,
                        low_confidence_inject=low_confidence,
                    )
                    assert policy.mode == (
                        "strict" if prompt_mode == "strict_kb" else "open"
                    )
                    assert policy.allow_uncited_user_content is user_content
                    assert policy.suppress_kb_citations is (
                        user_content
                        and low_confidence
                        and state
                        in policy_module.KB_ANSWER_POLICY_SUPPRESS_CITATION_STATES
                    )
                    meta = policy.to_kb_meta(
                        org_id="o",
                        user_id="u",
                        retrieval_ms=1,
                        render_mode="deterministic_non_streaming",
                    )
                    assert meta["answer_policy_mode"] == policy.mode
                    assert meta["chat_retrieval_prompt_mode"] == prompt_mode
                    assert meta["allow_uncited_user_content"] is user_content
                    assert meta["render_mode"] == "deterministic_non_streaming"


def test_prompt_prefix_matrix_keeps_user_content_scope_in_open_and_strict_kb_modes():
    policy_module = _policy_module()
    strict_prefix = policy_module.compose_kb_mode_chat_prefix(True, "KB BLOCK")
    open_prefix = policy_module.compose_kb_mode_chat_prefix(False, "KB BLOCK")

    assert policy_module.USER_PROVIDED_CONTENT_SCOPE in strict_prefix
    assert policy_module.USER_PROVIDED_CONTENT_SCOPE in open_prefix
    assert "KB BLOCK" in strict_prefix
    assert "KB BLOCK" in open_prefix
    assert strict_prefix != open_prefix


def test_retrieval_failure_notice_keeps_strict_closed_and_open_broad():
    policy_module = _policy_module()

    strict_notice = policy_module.kb_retrieval_failure_notice(True, "HTTP 503")
    assert "TEMPORARILY UNAVAILABLE" in strict_notice
    assert "selected Strict mode" in strict_notice
    assert "do not answer from general knowledge" in strict_notice
    assert "technical reason: HTTP 503" in strict_notice
    assert "Answer using your general knowledge" not in strict_notice

    open_notice = policy_module.kb_retrieval_failure_notice(False, "ReadTimeout")
    assert "TEMPORARILY UNAVAILABLE" in open_notice
    assert "Answer using your general knowledge" in open_notice
    assert "technical reason: ReadTimeout" in open_notice
    assert "not based on their own documentation" in open_notice
    assert "refresh or try again later" in open_notice
    assert "selected Strict mode" not in open_notice


def test_strict_kb_unavailable_message_lives_with_answer_policy():
    policy_module = _policy_module()

    assert (
        policy_module.strict_kb_unavailable_message("wat is de status?")
        == "De kennisbank is tijdelijk niet bereikbaar, dus ik kan dit niet "
        "betrouwbaar beantwoorden op basis van je kennisbronnen."
    )
    assert (
        policy_module.strict_kb_unavailable_message("what is the status?")
        == "The knowledge base is temporarily unavailable, so I cannot answer this "
        "reliably from your knowledge sources."
    )


def test_zero_chunks_notice_keeps_strict_closed_and_open_broad():
    policy_module = _policy_module()

    strict_notice = policy_module.kb_zero_chunks_notice(True)
    assert "zero results for this query" in strict_notice
    assert "Dat staat niet in de kennisbank" in strict_notice
    assert "Do not answer from general knowledge" in strict_notice
    assert "You may answer from your general knowledge" not in strict_notice

    open_notice = policy_module.kb_zero_chunks_notice(False)
    assert "zero results for this query" in open_notice
    assert "You may answer from your general knowledge" in open_notice
    assert (
        "Dit staat niet in jouw kennisbank, maar hier is een algemeen antwoord"
        in open_notice
    )
    assert "Do not answer from general knowledge" not in open_notice


def test_chunks_present_header_keeps_strict_closed_and_open_broad():
    policy_module = _policy_module()

    strict_header = policy_module.kb_chunks_present_header(True)
    assert "answer strictly using only the sources below" in strict_header
    assert "Do not use general knowledge beyond these sources" in strict_header
    assert "supplementary context" not in strict_header

    open_header = policy_module.kb_chunks_present_header(False)
    assert "use this as supplementary context" in open_header
    assert "You may complement it with your general knowledge" in open_header
    assert "answer strictly using only the sources below" not in open_header


def test_user_content_detection_requires_attachment_or_explicit_reference():
    policy_module = _policy_module()

    assert (
        policy_module.has_user_provided_content_context(
            [{"role": "user", "content": "Wat is het beleid?"}],
            "Wat is het beleid?",
        )
        is False
    )
    assert (
        policy_module.has_user_provided_content_context(
            [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "x"}}],
                }
            ],
            "Wat zie je?",
        )
        is True
    )
    assert (
        policy_module.has_user_provided_content_context(
            [{"role": "user", "content": "Leg dit uit"}],
            "Wat staat in deze screenshot?",
        )
        is False
    )
    assert (
        policy_module.has_user_provided_content_context(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Wat staat in deze screenshot?"},
                        {"type": "image_url", "image_url": {"url": "x"}},
                    ],
                }
            ],
            "Wat staat in deze screenshot?",
        )
        is True
    )
    assert (
        policy_module.has_user_provided_content_context(
            [{"role": "user", "content": "Mijn project heet Atlas."}],
            "Wat zei ik hierboven?",
        )
        is True
    )


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
    assert meta["kb_scope_mode"] is None
    assert meta["kbs_in_scope"] == []
    assert meta["kbs_with_results"] == []
    assert meta["kbs_used_as_sources"] == []


def test_mode_follows_prompt_mode():
    assert _policy("chunks_present", prompt_mode="strict_kb").mode == "strict"
    assert _policy("chunks_present", prompt_mode="strict_unavailable").mode == "strict"
    assert _policy("chunks_present", prompt_mode="open_kb").mode == "open"
    assert _policy("chunks_present", prompt_mode="open_unavailable").mode == "open"


def test_allow_uncited_user_content_tracks_user_provided_context():
    assert _policy("zero_chunks", upc=True).allow_uncited_user_content is True
    assert _policy("zero_chunks", upc=False).allow_uncited_user_content is False


def test_suppress_kb_citations_only_on_low_conf_chunk_states():
    # Requires BOTH user content AND low-confidence injection AND a chunk state.
    assert (
        _policy("chunks_present", upc=True, low_conf=True).suppress_kb_citations is True
    )
    assert _policy("zero_chunks", upc=True, low_conf=True).suppress_kb_citations is True
    # Not on non-chunk states even with the same flags.
    assert (
        _policy("retrieval_failure", upc=True, low_conf=True).suppress_kb_citations
        is False
    )
    assert (
        _policy("gate_bypassed", upc=True, low_conf=True).suppress_kb_citations is False
    )
    assert (
        _policy("missing_evidence_pack", upc=True, low_conf=True).suppress_kb_citations
        is False
    )
    # Needs both flags.
    assert (
        _policy("chunks_present", upc=True, low_conf=False).suppress_kb_citations
        is False
    )
    assert (
        _policy("chunks_present", upc=False, low_conf=True).suppress_kb_citations
        is False
    )


def test_to_kb_meta_propagates_policy_flags_into_metadata():
    meta = _policy(
        "chunks_present", prompt_mode="strict_kb", upc=True, low_conf=True
    ).to_kb_meta(org_id="o", user_id="u", retrieval_ms=1)
    assert meta["answer_policy_state"] == "chunks_present"
    assert meta["chat_retrieval_prompt_mode"] == "strict_kb"
    assert meta["answer_policy_mode"] == "strict"
    assert meta["kb_narrow"] is True
    assert meta["user_provided_content_context"] is True
    assert meta["low_confidence_inject"] is True
    assert meta["allow_uncited_user_content"] is True
    assert meta["suppress_kb_citations"] is True
