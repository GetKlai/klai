"""Regression tests for SUPPORT_BROAD_CHAT_SYSTEM_PROMPT and the broad-mode label.

The consented general-knowledge fallback of the public help-page widget. The
centre of the contract is the HARD boundary: broad mode is knowledge about the
world (what a SIP trunk is, how number porting works in the Netherlands), never
knowledge about the company (prices, features, settings, availability,
durations). An organisation-specific question the help articles do not answer
must still be refused even with broad mode on. These tests pin the prompt text
so a future edit cannot quietly soften that line into a confidence gradient.

They also guard the invariant that adding the broad profile did not touch the
other profiles: the language-detection preamble must stay byte-identical and
none of the existing prompts may absorb broad-mode wording.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    BROAD_MODE_ANSWER_MARKERS,
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
    META_CHAT_SYSTEM_PROMPT,
    OPEN_KB_CHAT_SYSTEM_PROMPT,
    SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
    broad_mode_answer_marker,
    is_broad_knowledge_answer,
)


def test_broad_prompt_is_non_empty():
    assert isinstance(SUPPORT_BROAD_CHAT_SYSTEM_PROMPT, str)
    assert len(SUPPORT_BROAD_CHAT_SYSTEM_PROMPT) > 500


def test_broad_prompt_shares_language_preamble_byte_for_byte():
    # Same contract as every other profile: the three language guards live in
    # one private constant and may never drift between modes.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    assert preamble_end > 0, "GROUNDED prompt structure changed unexpectedly"
    grounded_preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert SUPPORT_BROAD_CHAT_SYSTEM_PROMPT.startswith(grounded_preamble + "\n\n")


# ─── the hard boundary: world vs us ──────────────────────────────────────


def test_broad_prompt_declares_itself_a_fallback_after_the_articles():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "did not answer the visitor's question" in text
    assert "agreed that you may look" in text
    assert "AI assistant, not a human employee" in text


def test_broad_prompt_states_the_world_versus_us_line_as_the_single_test():
    # The boundary must be about the subject of the sentence, not certainty.
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "One test replaces every guess" in text
    assert "any other phone provider or" in text
    assert "only true for this company" in text
    assert "not about how certain you feel" in text


def test_broad_prompt_forbids_company_specific_claims_even_when_confident():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "even if you are sure you know it" in text
    assert "training data seems to" in text


@pytest.mark.parametrize(
    "term",
    [
        "prices",
        "rates",
        "plans",
        "contract terms",
        "feature availability and names",
        "product, module and plan names",
        "settings, menus",
        "outages",
        "delivery and processing times",
    ],
)
def test_broad_prompt_out_of_scope_list_covers_the_organisation_specific_kinds(term: str):
    assert term in SUPPORT_BROAD_CHAT_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "example",
    [
        "SIP trunk",
        "DECT",
        "number porting",
        "what an answering machine does",
    ],
)
def test_broad_prompt_in_scope_examples_are_general_domain_knowledge(example: str):
    # These are the exact categories the task names as allowed: explaining
    # them keeps the profile from over-refusing into uselessness.
    assert example in SUPPORT_BROAD_CHAT_SYSTEM_PROMPT


def test_broad_prompt_never_blends_world_and_company():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "Never blend the two" in text
    assert "including this one" in text


def test_broad_prompt_keeps_the_refusal_for_company_questions():
    # With broad mode on, an uncovered company question is still refused, in
    # customer words, with the help-articles phrasing (no 'kennisbank').
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "Ik vind dit niet terug in onze helpartikelen" in text
    assert "I can't find this in our help articles" in text
    assert "broad mode changes nothing" in text
    assert "point to support" in text


def test_broad_prompt_answers_partial_questions_per_part():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "answer the world-knowledge part and refuse the us-part explicitly" in text


def test_broad_prompt_keeps_no_promises_and_no_citation_markers():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "No promises on behalf of the company" in text
    assert "Do NOT commit to" in text
    assert "relaxes neither rule" in text
    assert "Do NOT write citation markers" in text
    assert "never" in text and "claim an answer comes from a help article" in text


def test_broad_prompt_holds_the_support_tone_and_friend_test():
    # Tone of voice invariants shared with the strict SUPPORT profile
    # (docs/research/voys-tone-of-voice.md § 10-11).
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "je/jij, never u" in text
    assert "Dit kan even duren" in text
    assert "Goed om te weten" in text
    assert "The friend test" in text
    assert "zou je dit tegen een vriend zeggen" in text
    assert "No emoji" in text


def test_broad_prompt_labels_are_applied_by_the_application_not_the_model():
    text = SUPPORT_BROAD_CHAT_SYSTEM_PROMPT
    assert "The application labels this answer as" in text
    assert "do not add your own disclaimer line" in text


# ─── the general-knowledge label (visible marking + gap detection) ────────


def test_broad_marker_picks_dutch_for_dutch_queries():
    assert broad_mode_answer_marker("wat is een sip trunk?") in BROAD_MODE_ANSWER_MARKERS
    assert broad_mode_answer_marker("Hoe werkt nummerportering in vredesnaam?") in BROAD_MODE_ANSWER_MARKERS
    assert "helpartikelen" in broad_mode_answer_marker("hoe stel ik iets in")
    assert "Algemene kennis" in broad_mode_answer_marker("wat is dect")


def test_broad_marker_picks_english_otherwise():
    marker = broad_mode_answer_marker("what is a sip trunk")
    assert "General knowledge" in marker
    assert "help articles" in marker
    # Non-string inputs must still produce a usable label (mirrors the refusal).
    assert broad_mode_answer_marker(None) in BROAD_MODE_ANSWER_MARKERS
    assert broad_mode_answer_marker({"meta": 1}) in BROAD_MODE_ANSWER_MARKERS


def test_broad_marker_language_pick_agrees_with_the_helpdesk_refusal():
    # Both user-visible canned strings must land in the same language for the
    # same query, or a refusal and a broad label would mix languages in one
    # conversation. Uses the same wordlist rule on purpose.
    from klai_chat_prompts import no_citable_sources_message

    for probe in ["wat is een sip trunk", "how do I configure the dial plan", "de", "", None]:
        dutch = "helpartikelen" in no_citable_sources_message(probe, helpdesk=True)
        marker = broad_mode_answer_marker(probe)
        assert ("helpartikelen" in marker) is dutch, f"language drift for probe {probe!r}"


def test_broad_markers_avoid_internal_jargon():
    for marker in BROAD_MODE_ANSWER_MARKERS:
        lowered = marker.lower()
        assert "kennisbank" not in lowered
        assert "knowledge base" not in lowered


def test_is_broad_knowledge_answer_matches_labelled_answers():
    nl = broad_mode_answer_marker("wat is dect")
    en = broad_mode_answer_marker("what is dect")
    assert is_broad_knowledge_answer(f"{nl}\n\nEen DECT-telefoon is draadloos.")
    assert is_broad_knowledge_answer(f"{en}\n\nA DECT phone is a cordless phone.")
    # Leading whitespace tolerated (the backend prepends the marker first).
    assert is_broad_knowledge_answer(f"  {nl}\n\nAntwoord.")


def test_is_broad_knowledge_answer_rejects_unlabelled_content():
    assert not is_broad_knowledge_answer("Gewoon antwoord uit de kennisbank.")
    assert not is_broad_knowledge_answer("")
    assert not is_broad_knowledge_answer(None)
    # The label mid-text (e.g. an article quoting it, or a visitor echoing it)
    # must NOT flip the detector: only a leading label counts.
    marker = broad_mode_answer_marker("klopt dit?")
    assert not is_broad_knowledge_answer(f"Hier staat: {marker}")
    # An unrelated German query must pick the English label and still be
    # recognised, because membership is checked over the whole label set.
    assert is_broad_knowledge_answer(f"{broad_mode_answer_marker('was ist dect')}\n\nAntwort")


# ─── the existing profiles stayed untouched ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "GROUNDED_CHAT_SYSTEM_PROMPT",
        "GENERAL_CHAT_SYSTEM_PROMPT",
        "OPEN_KB_CHAT_SYSTEM_PROMPT",
        "META_CHAT_SYSTEM_PROMPT",
        "SUPPORT_CHAT_SYSTEM_PROMPT",
    ],
)
def test_broad_mode_wording_did_not_leak_into_other_profiles(name: str):
    text = {
        "GROUNDED_CHAT_SYSTEM_PROMPT": GROUNDED_CHAT_SYSTEM_PROMPT,
        "GENERAL_CHAT_SYSTEM_PROMPT": GENERAL_CHAT_SYSTEM_PROMPT,
        "OPEN_KB_CHAT_SYSTEM_PROMPT": OPEN_KB_CHAT_SYSTEM_PROMPT,
        "META_CHAT_SYSTEM_PROMPT": META_CHAT_SYSTEM_PROMPT,
        "SUPPORT_CHAT_SYSTEM_PROMPT": SUPPORT_CHAT_SYSTEM_PROMPT,
    }[name]
    for leak in (
        "Broad mode",
        "broad mode",
        "world versus us",
        "One test replaces every guess",
        "Algemene kennis —",
        "General knowledge — not from our help articles",
    ):
        assert leak not in text, f"{leak!r} leaked into {name}"
