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
    META_CHAT_SYSTEM_PROMPT,
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
    assert "knowledge base chunks provided" not in text, "GENERAL prompt MUST NOT promise KB chunks — there are none."
    assert "Every factual claim gets a [n] citation" not in text, (
        "GENERAL prompt MUST NOT mandate [n] citations — without sources the model will fabricate citation markers."
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
    assert "Do NOT invent" in text, "GENERAL prompt missing the explicit anti-fabrication directive."
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


# ─── GROUNDED anti-confabulation guard ──────────────────────────────


def test_grounded_prompt_carries_anti_confabulation_block():
    """Regression for the 2026-05-12 Voys "Meldingen" incident: when the user
    questions WHY a previous answer was given, the model must not retrofit a
    justification by saying "dat staat in de kennisbank". The GROUNDED prompt
    now carries an explicit "When the user questions your reasoning" section
    that instructs the model to name actual chunks used, admit weak matches,
    and offer a re-try instead of defending the original answer.
    """
    text = GROUNDED_CHAT_SYSTEM_PROMPT
    # Section header — anchors the rule so a future edit that removes it
    # surfaces immediately.
    assert "When the user questions your reasoning" in text, (
        "GROUNDED prompt missing the anti-confabulation section. The Voys "
        "incident retrospect required a dedicated section, not just a "
        "passing mention."
    )
    # Bilingual trigger phrasings — Klai's primary user-facing language is
    # NL; English coverage is required for partner-API surfaces.
    text_lower = text.lower()
    assert "waarom kom je met dit antwoord" in text_lower
    assert "why this?" in text_lower or "where does that come from" in text_lower
    # Positive instruction: name the actual chunks used.
    assert "name the actual chunks" in text_lower
    # Explicit forbid: never use the "it's in the KB" non-answer.
    assert "non-answer" in text_lower, (
        "GROUNDED prompt must explicitly mark 'it's in the KB' as a "
        "non-answer — otherwise the model keeps reaching for it."
    )


# ─── META_CHAT_SYSTEM_PROMPT regression tests ────────────────────────


def test_meta_prompt_is_non_empty():
    assert isinstance(META_CHAT_SYSTEM_PROMPT, str)
    assert len(META_CHAT_SYSTEM_PROMPT) > 500


def test_meta_prompt_carries_critical_marker():
    # The shared language-detection preamble starts with [CRITICAL].
    assert "[CRITICAL]" in META_CHAT_SYSTEM_PROMPT


def test_meta_prompt_inherits_three_guards_from_preamble():
    # The 3-guard contract is shared across GROUNDED / GENERAL / META via
    # the private _LANGUAGE_DETECTION_PREAMBLE. If a refactor drops the
    # preamble from META, this test fails loud instead of silently
    # drifting.
    text = META_CHAT_SYSTEM_PROMPT.lower()
    assert "substantive" in text
    assert "5 words" in META_CHAT_SYSTEM_PROMPT
    assert "single foreign-language words" in text or "single foreign" in text
    assert "stays switched" in text


def test_meta_prompt_includes_klai_ai_identity():
    assert "Klai AI" in META_CHAT_SYSTEM_PROMPT


def test_meta_prompt_marks_question_as_meta():
    # The whole point of this prompt: instruct the model that the user is
    # asking ABOUT Klai, not asking a content question. If a future edit
    # softens this framing, the model falls back to source-quoting mode and
    # the Voys-style failure reopens.
    text = META_CHAT_SYSTEM_PROMPT
    assert "META question" in text or "meta question" in text.lower()
    assert "NOT asking a question about the content" in text


def test_meta_prompt_forbids_citation_and_quoting():
    # META MUST NOT cite [n] — there are no chunks in scope.
    text = META_CHAT_SYSTEM_PROMPT
    assert "Do NOT add [n] citations" in text, (
        "META prompt MUST explicitly forbid [n] citations — without sources the model would fabricate citation markers."
    )
    assert "Do NOT quote from any document" in text


def test_meta_prompt_forbids_feature_fabrication():
    # The specific anti-pattern: Klai inventing feature names that don't
    # exist. The phrasing must be unambiguous, not just hint.
    text = META_CHAT_SYSTEM_PROMPT
    assert "Do NOT invent specific Klai features" in text


def test_meta_prompt_requires_generic_example_phrasing():
    # The model is asked to give 2-3 example questions, but PHRASED
    # generically with placeholders. The exact regression we want to
    # prevent: model invents "voorbeeld: wat is onze pricing voor X?" with
    # invented product names.
    text = META_CHAT_SYSTEM_PROMPT
    assert "GENERICALLY" in text
    assert "Do NOT invent specific product names" in text


def test_meta_prompt_describes_klai_at_capability_level():
    # The "what kind of thing it is" description must mention:
    # - org knowledge (KB grounding)
    # - multilingual
    # - KB selector (the chat surface UI affordance)
    # - Web Search escape hatch
    text = META_CHAT_SYSTEM_PROMPT
    assert "organization's knowledge" in text or "organization knowledge" in text.lower()
    assert "any language" in text.lower()
    assert "knowledge-base selector" in text.lower() or "kb selector" in text.lower()
    assert "Web Search" in text


def test_meta_prompt_forbids_sycophancy_and_emoji():
    # Specific anti-pattern from the Voys incident: the response ended
    # with "Bedankt! 😊" — sycophancy + emoji. META must explicitly
    # forbid both so the model doesn't default back to filler.
    text = META_CHAT_SYSTEM_PROMPT
    assert "Do NOT use warm-up filler" in text
    assert "Do NOT use emoji" in text


def test_meta_prompt_shares_preamble_byte_for_byte_with_grounded():
    # META and GROUNDED MUST share the identical language-detection
    # preamble. Same invariant as the GROUNDED ↔ GENERAL pair, extended
    # to the third prompt now in play.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    assert preamble_end > 0, "GROUNDED prompt structure changed unexpectedly"
    grounded_preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert META_CHAT_SYSTEM_PROMPT.startswith(grounded_preamble + "\n\n"), (
        "META and GROUNDED must share an identical language-detection "
        "preamble. Refactor _LANGUAGE_DETECTION_PREAMBLE if you need to "
        "change it — never edit one of the public constants in isolation."
    )


def test_all_three_prompts_share_identical_preamble():
    # Defence-in-depth: the previous two pairwise tests cover GROUNDED ↔
    # GENERAL and GROUNDED ↔ META. This third test asserts the full
    # three-way equality so a future refactor can't accidentally derive
    # the preamble from two of the three.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert GENERAL_CHAT_SYSTEM_PROMPT.startswith(preamble + "\n\n")
    assert META_CHAT_SYSTEM_PROMPT.startswith(preamble + "\n\n")
