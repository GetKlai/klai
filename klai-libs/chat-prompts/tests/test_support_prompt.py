"""Regression tests for SUPPORT_CHAT_SYSTEM_PROMPT.

The public help-page widget variant of GROUNDED. These guard the
structural invariants of the profile — that it reuses the shared
language-detection preamble (so the three guards can never drift from
GROUNDED) and keeps the customer-support rules the profile was written to
add. As with the other prompt tests, they do not assert LLM behaviour;
they assert the prompt text stays intact under future edits.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    GROUNDED_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
)


def test_support_prompt_is_non_empty():
    assert isinstance(SUPPORT_CHAT_SYSTEM_PROMPT, str)
    assert len(SUPPORT_CHAT_SYSTEM_PROMPT) > 500


def test_support_prompt_carries_critical_marker_and_identity():
    assert "[CRITICAL]" in SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Klai AI" in SUPPORT_CHAT_SYSTEM_PROMPT


def test_support_prompt_shares_language_preamble_byte_for_byte():
    # The whole point of putting the three guards in a private constant:
    # SUPPORT and GROUNDED MUST open with an identical language-detection
    # preamble. If a refactor derives one profile's preamble from the
    # other in isolation, SPEC-RAG-MULTILINGUAL-CHAT-001 is broken.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    assert preamble_end > 0, "GROUNDED prompt structure changed unexpectedly"
    grounded_preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert SUPPORT_CHAT_SYSTEM_PROMPT.startswith(grounded_preamble + "\n\n")
    # And the shared three guards are actually present.
    text = SUPPORT_CHAT_SYSTEM_PROMPT.lower()
    assert "substantive" in text
    assert "5 words" in SUPPORT_CHAT_SYSTEM_PROMPT
    assert "stays switched" in text


def test_support_prompt_declares_itself_an_ai_not_a_human():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "AI support assistant" in text
    assert "not a human employee" in text
    assert "never claim to be one" in text


def test_support_prompt_is_customer_friendly_businesslike():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Customer-friendly but businesslike" in text
    assert "No emoji" in text
    # je/jij-vorm for Dutch visitors.
    assert "je/jij" in text


def test_support_prompt_uses_customer_words_for_missing_answer():
    # The core fix vs GROUNDED: a visitor does not know what a "kennisbank"
    # is, so the missing-answer fallback speaks of help articles instead.
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Ik vind dit niet terug in onze helpartikelen" in text
    assert "I can't find this in our help articles" in text
    assert "helpartikelen" in text


def test_support_prompt_names_kennisbank_only_as_a_prohibition():
    # "kennisbank"/"knowledge base" may appear ONLY in the instruction that
    # forbids the model from using them in front of a visitor — never as an
    # affirmative term the model would repeat back.
    for line in SUPPORT_CHAT_SYSTEM_PROMPT.splitlines():
        lowered = line.lower()
        if "kennisbank" in lowered or "knowledge base" in lowered:
            assert "do not use" in lowered, f"kennisbank/knowledge base leaked outside the prohibition: {line!r}"


def test_support_prompt_limits_clarifying_questions_to_one():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "AT MOST ONE short" in text
    assert "clarifying question" in text


def test_support_prompt_keeps_multi_part_behaviour():
    # GROUNDED already answers multi-question messages per question; the
    # support profile preserves that behaviour (with customer wording).
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Multi-part questions" in text
    assert "answer PER QUESTION" in text
    assert "number of answers MUST equal" in text


def test_support_prompt_procedures_keep_source_labels():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "numbered steps" in text
    assert "exactly as they appear in the help article" in text


def test_support_prompt_forbids_company_commitments():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "No promises on behalf of the company" in text
    assert "Do NOT commit to" in text
    for term in ("delivery times", "prices", "goodwill", "outage"):
        assert term in text


def test_support_prompt_escalates_to_support_without_a_human_handoff():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    # No "talk to an agent" affordance exists in this setup.
    assert "cannot transfer this chat to a person" in text
    assert "must NOT offer to" in text
    # Frustration / repeat complaints / cancel / outage / pricing / contract
    # all route to the support department.
    assert "frustrated" in text
    assert "wants to cancel" in text
    assert "reports an outage" in text
    assert "pricing or contract question" in text
    assert "support department" in text
    assert "contact details on this website" in text


def test_support_prompt_does_not_invent_contact_details():
    # The phone/e-mail ban is explicit: no specific number or address may be
    # fabricated, since none is provided in the prompt.
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Do NOT invent or display a specific phone number" in text
    assert "you do not have one" in text


def test_support_prompt_keeps_source_citation_backend_managed():
    # Same contract as GROUNDED: the model writes no citation markers or
    # URLs; the application adds trusted sources after generation.
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "Do NOT write citation markers" in text
    assert "renders trusted sources separately" in text
    assert "Every factual claim gets a [n] citation" not in text


def test_support_prompt_has_no_warm_up_filler():
    text = SUPPORT_CHAT_SYSTEM_PROMPT
    assert "great question!" in text  # named as an example of what to avoid
    assert "No rephrasing the question" in text


@pytest.mark.parametrize("phrase", ["Dat staat niet in de kennisbank", "senior colleague"])
def test_support_prompt_does_not_carry_internalkb_phrasing(phrase: str):
    # "senior colleague" is the internal-team GROUNDED voice; the support
    # profile addresses a visitor, not a colleague. "Dat staat niet in de
    # kennisbank" is the KB-jargon fallback. Neither belongs here.
    assert phrase not in SUPPORT_CHAT_SYSTEM_PROMPT
