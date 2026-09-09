"""Tests for the language-code-based ``no_citable_sources_message`` helper.

The helper exists so the canned strict-mode refusal speaks the language the
conversation was DECIDED in. It takes a language code, never raw user text:
path A (LiteLLM hook) passes the conversation decision, paths B (partner_chat)
and C (synthesis) pass the output of the single-text identifier
``klai_chat_prompts.language.identify_text_language``. Same source of truth
for all three; a drift test in deploy/litellm/tests/ guards the vendored copy.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import no_citable_sources_message

_DUTCH = "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."
_ENGLISH = "I cannot answer this reliably from the available knowledge sources."
_DUTCH_WITH_HINT = _DUTCH + " Probeer het in Open-modus voor een antwoord op basis van algemene kennis."
_ENGLISH_WITH_HINT = _ENGLISH + " Try Open mode for an answer based on general knowledge."


def test_dutch_code_returns_dutch_refusal() -> None:
    assert no_citable_sources_message("nl") == _DUTCH


@pytest.mark.parametrize("code", ["en", "nl-NL", "DE", "de"])
def test_exact_code_match_is_case_and_locale_strict(code: str) -> None:
    # Only the bare lowercase decision code selects Dutch; a locale variant
    # or uppercase slip is a caller bug we refuse to paper over — it renders
    # the documented English fallback.
    assert no_citable_sources_message(code) == _ENGLISH


@pytest.mark.parametrize("language", [None, "und", 42, {"lang": "nl"}, ["nl"], "", "   "])
def test_missing_or_non_string_language_falls_back_to_dutch(language: object) -> None:
    # No decision (None / "und" / blank / garbage) renders DUTCH, never an
    # empty refusal. Measured 2026-09-09: the identifier abstains on half of
    # typical short Dutch widget questions ("Hoe log ik in?") but decides
    # nearly all equally short English ones, and Klai's customers are
    # overwhelmingly Dutch — so an undecided turn is far more likely Dutch.
    # An explicit non-Dutch code still renders English (test above).
    assert no_citable_sources_message(language) == _DUTCH


def test_suggest_open_mode_defaults_false_no_hint() -> None:
    assert no_citable_sources_message("nl") == _DUTCH
    assert no_citable_sources_message("en") == _ENGLISH


def test_suggest_open_mode_true_appends_dutch_hint() -> None:
    assert no_citable_sources_message("nl", suggest_open_mode=True) == _DUTCH_WITH_HINT


def test_suggest_open_mode_true_appends_english_hint() -> None:
    assert no_citable_sources_message("en", suggest_open_mode=True) == _ENGLISH_WITH_HINT


def test_suggest_open_mode_true_missing_language_falls_through_dutch() -> None:
    assert no_citable_sources_message(None, suggest_open_mode=True) == _DUTCH_WITH_HINT


# ─── helpdesk variant (public help-page widget) ──────────────────────────

_DUTCH_HELPDESK = (
    "Dit vind ik niet terug in onze helpartikelen. "
    "Wil je het zeker weten, plan dan een afspraak met een medewerker — die helpt je persoonlijk verder."
)
_ENGLISH_HELPDESK = (
    "I can't find this in our help articles. "
    "If you want to be sure, schedule an appointment with someone who can help you personally."
)


@pytest.mark.parametrize("language", ["nl", "und", None, 42, "", "   ", {"lang": "nl"}])
def test_helpdesk_variant_dutch_on_nl_code_and_on_no_decision(language: object) -> None:
    # Same fallback contract as the plain refusal: undecided renders Dutch.
    assert no_citable_sources_message(language, helpdesk=True) == _DUTCH_HELPDESK


@pytest.mark.parametrize("language", ["en", "de", "DE", "nl-NL"])
def test_helpdesk_variant_falls_through_to_english_on_explicit_other_code(language: str) -> None:
    assert no_citable_sources_message(language, helpdesk=True) == _ENGLISH_HELPDESK


@pytest.mark.parametrize(
    "language,expected",
    [
        ("nl", _DUTCH_HELPDESK),
        ("en", _ENGLISH_HELPDESK),
    ],
)
def test_helpdesk_variant_ignores_suggest_open_mode(language: str, expected: str) -> None:
    # The help-page widget has no Strict/Open toggle, so the Open-mode hint
    # is meaningless here — helpdesk must override it, not append to it.
    assert no_citable_sources_message(language, helpdesk=True, suggest_open_mode=True) == expected


@pytest.mark.parametrize("helpdesk", [True, False])
def test_helpdesk_variant_never_uses_kb_jargon(helpdesk: bool) -> None:
    # Customer-facing wording: "kennisbank" / "kennisbronnen" /
    # "knowledge sources" are internal terms a website visitor does not
    # know. The helpdesk refusal must avoid them in both languages. The
    # non-helpdesk refusal is the one that legitimately says
    # "kennisbronnen"; only the helpdesk variant is jargon-free.
    if not helpdesk:
        return
    for language in ("nl", "en"):
        refusal = no_citable_sources_message(language, helpdesk=True)
        lowered = refusal.lower()
        assert "kennisbank" not in lowered
        assert "kennisbron" not in lowered
        assert "knowledge source" not in lowered


def test_helpdesk_variant_names_the_next_step() -> None:
    """The refusal bypasses the system prompt, so it carries the brand voice on
    its own. It must point at a concrete next step the visitor can take — an
    appointment — rather than at a department, which the brand documentation
    lists under what does not work."""
    en = no_citable_sources_message("en", helpdesk=True).lower()
    assert "appointment" in en
    nl = no_citable_sources_message("nl", helpdesk=True).lower()
    assert "afspraak" in nl
    # And never the phrasing the brand doc rejects.
    assert "klantenservice afdeling" not in nl
    assert "contact op met de support" not in nl


def test_helpdesk_default_leaves_existing_callers_untouched() -> None:
    # Regression: helpdesk defaults to False, so the existing path A/B/C
    # refusal text is byte-for-byte unchanged when the flag is not passed.
    assert no_citable_sources_message("nl") == _DUTCH
    assert no_citable_sources_message("en") == _ENGLISH
