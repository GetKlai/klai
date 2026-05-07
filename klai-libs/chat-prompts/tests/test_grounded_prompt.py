"""Regression tests for GROUNDED_CHAT_SYSTEM_PROMPT and
GENERAL_CHAT_SYSTEM_PROMPT.

These tests don't try to assert that the LLM behaves correctly — they
only guard the structural invariants of the prompt strings, so that a
future edit can't silently strip out one of the guards SPEC-RAG-
MULTILINGUAL-CHAT-001 REQ-01 mandates, and so the GENERAL prompt can
never accidentally drift back into the GROUNDED rule set (which is the
exact regression GENERAL was introduced to fix).
"""

from __future__ import annotations

from klai_chat_prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
)


def test_prompt_is_non_empty():
    assert isinstance(GROUNDED_CHAT_SYSTEM_PROMPT, str)
    assert len(GROUNDED_CHAT_SYSTEM_PROMPT) > 200


def test_prompt_carries_critical_marker():
    # Marker that the LLM should read as a hard instruction, not a hint.
    assert "[CRITICAL]" in GROUNDED_CHAT_SYSTEM_PROMPT


def test_prompt_includes_substantive_message_concept():
    # Guard 1: substantive vs short messages.
    text = GROUNDED_CHAT_SYSTEM_PROMPT.lower()
    assert "substantive" in text


def test_prompt_includes_minimum_word_threshold():
    # Guard 1: fewer-than-5-words rule must remain.
    assert "5 words" in GROUNDED_CHAT_SYSTEM_PROMPT


def test_prompt_includes_single_foreign_word_guard():
    # Guard 2: brief foreign acknowledgements don't flip the conversation.
    text = GROUNDED_CHAT_SYSTEM_PROMPT.lower()
    assert "single foreign-language words" in text or "single foreign" in text


def test_prompt_includes_substantive_switch_guard():
    # Guard 3: real switch on a full substantive message.
    text = GROUNDED_CHAT_SYSTEM_PROMPT.lower()
    assert "switched" in text
    assert "stays switched" in text


def test_prompt_does_not_hardcode_dutch_language_switch():
    # The whole point of this SPEC: the old `Als de gebruiker Nederlands
    # schrijft` line is GONE. If a future edit reintroduces it, this
    # test fails first and the CI lint catches the second-order impact.
    text = GROUNDED_CHAT_SYSTEM_PROMPT
    assert "Als de gebruiker Nederlands schrijft" not in text
    assert "Never switch mid-conversation" not in text


def test_prompt_includes_citation_format_instruction():
    # Citations [n] format must remain intact — downstream parsers depend
    # on it.
    text = GROUNDED_CHAT_SYSTEM_PROMPT.lower()
    assert "[n] citation" in text
    assert "factual claim" in text


def test_prompt_includes_no_disclaimer_directive():
    text = GROUNDED_CHAT_SYSTEM_PROMPT.lower()
    assert "do not apologize" in text
    assert "do not add translator disclaimers" in text


def test_prompt_includes_klai_ai_identity():
    assert "Klai AI" in GROUNDED_CHAT_SYSTEM_PROMPT


def test_prompt_describes_not_in_kb_fallback_multilingual():
    # The "answer isn't there" fallback must show the user's language —
    # not pin to one canonical phrase.
    text = GROUNDED_CHAT_SYSTEM_PROMPT
    assert "user's language" in text
    # Quick canary: at least three example phrases (NL/EN/DE) are listed.
    assert "knowledge base" in text.lower()
    assert "kennisbank" in text.lower()
    assert "Wissensdatenbank" in text


# ─── GENERAL_CHAT_SYSTEM_PROMPT regression tests ─────────────────────


def test_general_prompt_is_non_empty():
    assert isinstance(GENERAL_CHAT_SYSTEM_PROMPT, str)
    assert len(GENERAL_CHAT_SYSTEM_PROMPT) > 200


def test_general_prompt_carries_critical_marker():
    # The shared language-detection preamble starts with [CRITICAL].
    assert "[CRITICAL]" in GENERAL_CHAT_SYSTEM_PROMPT


def test_general_prompt_inherits_three_guards_from_preamble():
    # The 3-guard contract is shared between GROUNDED and GENERAL via
    # the private _LANGUAGE_DETECTION_PREAMBLE. If a refactor breaks
    # that share, this test fails loud instead of silently drifting.
    text = GENERAL_CHAT_SYSTEM_PROMPT.lower()
    assert "substantive" in text
    assert "5 words" in GENERAL_CHAT_SYSTEM_PROMPT
    assert "single foreign-language words" in text or "single foreign" in text
    assert "stays switched" in text


def test_general_prompt_identifies_as_general_purpose_assistant():
    # The whole point of this prompt: model behaves as a general AI,
    # not a KB-grounded assistant.
    assert "general-purpose assistant" in GENERAL_CHAT_SYSTEM_PROMPT


def test_general_prompt_forbids_kb_grounding_phrases():
    # If any of these strings reappear in GENERAL, the model will fall
    # back to KB-RAG behaviour even though the user explicitly opted
    # out of every scope. This is the exact regression the prompt was
    # introduced to prevent.
    text = GENERAL_CHAT_SYSTEM_PROMPT
    assert "Dat staat niet in de kennisbank" not in text, (
        "GENERAL prompt MUST NOT include the GROUNDED 'answer not in KB' "
        "fallback — it instructs the model to refuse general-knowledge "
        "questions when no KB is in scope."
    )
    assert "knowledge base chunks provided" not in text, (
        "GENERAL prompt MUST NOT promise KB chunks — there are none."
    )
    assert "Every factual claim gets a [n] citation" not in text, (
        "GENERAL prompt MUST NOT mandate [n] citations — without sources "
        "the model will fabricate citation markers."
    )


def test_general_prompt_explicitly_disables_citations_and_kb_pretense():
    # Positive form of the previous test: GENERAL must SAY 'no citations,
    # no pretending', not just omit the GROUNDED rules.
    text = GENERAL_CHAT_SYSTEM_PROMPT
    assert "Do NOT add [n] citations" in text
    assert "Do NOT pretend to have sources" in text


def test_general_prompt_includes_klai_ai_identity():
    assert "Klai AI" in GENERAL_CHAT_SYSTEM_PROMPT


def test_general_and_grounded_share_language_preamble_byte_for_byte():
    # Both prompts MUST start with the identical language-detection
    # preamble. Drift here means the 3 guards behave differently in
    # general-mode than in grounded-mode — that asymmetry is exactly
    # what SPEC-RAG-MULTILINGUAL-CHAT-001 forbids.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    assert preamble_end > 0, "GROUNDED prompt structure changed unexpectedly"
    grounded_preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert GENERAL_CHAT_SYSTEM_PROMPT.startswith(grounded_preamble + "\n\n"), (
        "GENERAL and GROUNDED must share an identical language-detection "
        "preamble. Refactor _LANGUAGE_DETECTION_PREAMBLE if you need to "
        "change it — never edit one of the public constants in isolation."
    )


def test_general_prompt_carries_anti_hallucination_block():
    # Regression for the 2026-05-07 followup: when no KB AND no Web
    # Search tool is wired in for the chat, the model must refuse to
    # fabricate company / product / URL facts and must point the user
    # at Web Search or KB selection. The original GENERAL prompt only
    # said "answer from general knowledge" — for "what's on company
    # X's website?" that produced a hallucinated voys.nl + invented
    # tagline ("volledig groene telefonie"). The follow-up adds an
    # explicit anti-fabrication block.
    text = GENERAL_CHAT_SYSTEM_PROMPT
    assert "Do NOT invent" in text, (
        "GENERAL prompt missing the explicit anti-fabrication directive."
    )
    # Must reference the Web Search escape hatch by name so the model
    # tells the user where to enable it.
    assert "Web Search" in text, (
        "GENERAL prompt must point users at Web Search as the live-lookup "
        "escape hatch — without this hint the user keeps getting "
        "hallucinations and doesn't know where to switch the lookup on."
    )
    # Must also mention KB selection as the second escape hatch.
    assert "knowledge base" in text.lower()
    # Must explicitly forbid fabricating URLs/domains — that was the
    # exact regression: 'voys.nl' invented out of thin air.
    assert "URL" in text
