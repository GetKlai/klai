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
