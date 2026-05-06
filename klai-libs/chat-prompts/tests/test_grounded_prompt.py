"""Regression tests for GROUNDED_CHAT_SYSTEM_PROMPT.

These tests don't try to assert that the LLM behaves correctly — they
only guard the structural invariants of the prompt string itself, so
that a future edit can't silently strip out one of the guards SPEC-RAG-
MULTILINGUAL-CHAT-001 REQ-01 mandates.
"""

from __future__ import annotations

from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT


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
