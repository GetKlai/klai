"""Tests for the language-aware ``no_citable_sources_message`` helper.

The helper exists so the canned strict-mode refusal speaks Dutch when
the user typed Dutch and English otherwise. Same source of truth for
both the LiteLLM hook (path A) and partner_chat.py (path B); a drift
test in deploy/litellm/tests/ guards the vendored copy.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import DUTCH_QUERY_MARKERS, no_citable_sources_message

_DUTCH = "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."
_ENGLISH = "I cannot answer this reliably from the available knowledge sources."
_DUTCH_WITH_HINT = _DUTCH + " Probeer het in Open-modus voor een antwoord op basis van algemene kennis."
_ENGLISH_WITH_HINT = _ENGLISH + " Try Open mode for an answer based on general knowledge."


@pytest.mark.parametrize(
    "query",
    [
        "Wat is dit?",
        "Wie is Jantine?",
        "Hoe werkt dit precies?",
        "Hoeveel kost een abonnement?",
        "Waar staat onze handleiding?",
        "Wanneer worden de gegevens bijgewerkt?",
        "Welke kennisbank moet ik kiezen?",
        "Klopt het dat ik geen bronnen kan toevoegen?",
        # Mixed casing
        "WAAR STAAT DIT?",
    ],
)
def test_dutch_queries_return_dutch_refusal(query: str) -> None:
    assert no_citable_sources_message(query) == _DUTCH


@pytest.mark.parametrize(
    "query",
    [
        "What is this?",
        "Who is Jantine?",
        "How does this work exactly?",
        "How much does a subscription cost?",
        "Where can I find our manual?",
        # Names that previously could have tripped the heuristic
        "Ben Affleck",
        "Khan Academy review",
        # Empty / malformed inputs fall through to English
        "",
        "   ",
        "12345",
    ],
)
def test_non_dutch_queries_return_english_refusal(query: str) -> None:
    assert no_citable_sources_message(query) == _ENGLISH


@pytest.mark.parametrize("query", [None, 42, {"text": "Wat is dit?"}, ["wat"]])
def test_non_string_inputs_return_english_refusal(query: object) -> None:
    assert no_citable_sources_message(query) == _ENGLISH


def test_marker_set_contains_no_single_letter_or_short_tokens() -> None:
    """Single-letter / two-letter tokens are too easy to false-positive on
    English text. Keep the set free of them.

    "de", "ik", "ze", "je", "wat", "het", "een" etc are short but
    UNAMBIGUOUSLY Dutch — they're allowed. The guard here is purely
    against single-letter slip-ins (e.g. the old set contained "u").
    """
    for token in DUTCH_QUERY_MARKERS:
        assert len(token) >= 2, f"single-letter token leaked into markers: {token!r}"


def test_marker_set_is_lowercase() -> None:
    for token in DUTCH_QUERY_MARKERS:
        assert token == token.lower(), f"marker not lowercase: {token!r}"


def test_suggest_open_mode_defaults_false_no_hint() -> None:
    assert no_citable_sources_message("Wat is dit?") == _DUTCH
    assert no_citable_sources_message("What is this?") == _ENGLISH


def test_suggest_open_mode_true_appends_dutch_hint() -> None:
    assert no_citable_sources_message("Wat is dit?", suggest_open_mode=True) == _DUTCH_WITH_HINT


def test_suggest_open_mode_true_appends_english_hint() -> None:
    assert no_citable_sources_message("What is this?", suggest_open_mode=True) == _ENGLISH_WITH_HINT


def test_suggest_open_mode_true_non_string_query_falls_through_english() -> None:
    assert no_citable_sources_message(None, suggest_open_mode=True) == _ENGLISH_WITH_HINT


# ─── helpdesk variant (public help-page widget) ──────────────────────────

_DUTCH_HELPDESK = (
    "Dit vind ik niet terug in onze helpartikelen. "
    "Wil je het zeker weten, plan dan een afspraak met een medewerker — die helpt je persoonlijk verder."
)
_ENGLISH_HELPDESK = (
    "I can't find this in our help articles. "
    "If you want to be sure, schedule an appointment with someone who can help you personally."
)


@pytest.mark.parametrize("query", ["Waarom lukt dit niet?", "Hoe vraag ik een refund aan?", "WAAROM?"])
def test_helpdesk_variant_follows_dutch_query_language(query: str) -> None:
    assert no_citable_sources_message(query, helpdesk=True) == _DUTCH_HELPDESK


@pytest.mark.parametrize("query", ["Why not?", "how do I get a refund?", "", "   ", "12345", None, 42])
def test_helpdesk_variant_falls_through_to_english(query: object) -> None:
    assert no_citable_sources_message(query, helpdesk=True) == _ENGLISH_HELPDESK


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Waarom lukt dit niet?", _DUTCH_HELPDESK),
        ("Why not?", _ENGLISH_HELPDESK),
    ],
)
def test_helpdesk_variant_ignores_suggest_open_mode(query: str, expected: str) -> None:
    # The help-page widget has no Strict/Open toggle, so the Open-mode hint
    # is meaningless here — helpdesk must override it, not append to it.
    assert no_citable_sources_message(query, helpdesk=True, suggest_open_mode=True) == expected


@pytest.mark.parametrize("helpdesk", [True, False])
def test_helpdesk_variant_never_uses_kb_jargon(helpdesk: bool) -> None:
    # Customer-facing wording: "kennisbank" / "kennisbronnen" /
    # "knowledge sources" are internal terms a website visitor does not
    # know. The helpdesk refusal must avoid them in both languages. The
    # non-helpdesk refusal is the one that legitimately says
    # "kennisbronnen"; only the helpdesk variant is jargon-free.
    if not helpdesk:
        return
    for query in ("Waarom lukt dit niet?", "Why not?"):
        refusal = no_citable_sources_message(query, helpdesk=True)
        lowered = refusal.lower()
        assert "kennisbank" not in lowered
        assert "kennisbron" not in lowered
        assert "knowledge source" not in lowered


def test_helpdesk_variant_names_the_next_step() -> None:
    """The refusal bypasses the system prompt, so it carries the brand voice on
    its own. It must point at a concrete next step the visitor can take — an
    appointment — rather than at a department, which the brand documentation
    lists under what does not work."""
    en = no_citable_sources_message("Why not?", helpdesk=True).lower()
    assert "appointment" in en
    nl = no_citable_sources_message("Waarom?", helpdesk=True).lower()
    assert "afspraak" in nl
    # And never the phrasing the brand doc rejects.
    assert "klantenservice afdeling" not in nl
    assert "contact op met de support" not in nl


def test_helpdesk_default_leaves_existing_callers_untouched() -> None:
    # Regression: helpdesk defaults to False, so the existing path A/B/C
    # refusal text is byte-for-byte unchanged when the flag is not passed.
    assert no_citable_sources_message("Waarom lukt dit niet?") == _DUTCH
    assert no_citable_sources_message("Why not?") == _ENGLISH
