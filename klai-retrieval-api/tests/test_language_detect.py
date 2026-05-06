"""Unit tests for retrieval_api.util.language_detect.

Covers SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07 detection module: per-text
ISO-639-1 detection with UNKNOWN_LANGUAGE fallback for short input,
unsupported languages, and detector errors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from retrieval_api.util.language_detect import (
    UNKNOWN_LANGUAGE,
    detect_language,
    language_correctness,
)

# -- detect_language ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "  ", None, "short"],
)
def test_detect_language_returns_unknown_for_short_or_empty_input(text):
    assert detect_language(text or "") == UNKNOWN_LANGUAGE


def test_detect_language_dutch_realistic_input():
    # Long enough sample to clear the 30-char threshold; lingua should
    # confidently return "nl".
    text = (
        "Hoe stel ik tweefactorauthenticatie in via de instellingen "
        "van de portal? Ik wil dit graag activeren voor mijn account."
    )
    assert detect_language(text) == "nl"


def test_detect_language_english_realistic_input():
    text = (
        "How can I configure single sign-on with our identity provider? "
        "I want to make sure the redirect URIs are set correctly."
    )
    assert detect_language(text) == "en"


def test_detect_language_german_realistic_input():
    text = (
        "Wie konfiguriere ich Single Sign-On mit unserem Identitätsanbieter? "
        "Ich möchte sicherstellen, dass die Weiterleitungs-URIs korrekt sind."
    )
    assert detect_language(text) == "de"


def test_detect_language_french_realistic_input():
    text = (
        "Comment puis-je configurer l'authentification unique avec notre "
        "fournisseur d'identité? Je veux m'assurer que les URI de redirection sont correctes."
    )
    assert detect_language(text) == "fr"


def test_detect_language_portuguese_realistic_input():
    text = (
        "Como posso configurar o single sign-on com o nosso provedor de identidade? "
        "Quero garantir que os URIs de redirecionamento estão corretos."
    )
    assert detect_language(text) == "pt"


def test_detect_language_spanish_realistic_input():
    text = (
        "¿Cómo puedo configurar el inicio de sesión único con nuestro proveedor "
        "de identidad? Quiero asegurarme de que las URI de redirección sean correctas."
    )
    assert detect_language(text) == "es"


def test_detect_language_returns_unknown_for_unsupported_target():
    # Italian is in the detector's allow-list but not a target language;
    # it MUST fall through to UNKNOWN_LANGUAGE.
    text = (
        "Come posso configurare il single sign-on con il nostro provider "
        "di identità? Voglio essere sicuro che gli URI di reindirizzamento siano corretti."
    )
    assert detect_language(text) == UNKNOWN_LANGUAGE


def test_detect_language_returns_unknown_when_lingua_unavailable(monkeypatch):
    # Reset the cached singleton then force the import-time fallback.
    import retrieval_api.util.language_detect as mod

    monkeypatch.setattr(mod, "_lingua_detector", None)
    sample = "This is a sufficiently long English sample for detection."
    with patch.object(mod, "_get_lingua_detector", return_value=None):
        assert mod.detect_language(sample) == UNKNOWN_LANGUAGE


def test_detect_language_returns_unknown_on_detector_exception(monkeypatch):
    # Cached detector raises on detect_language_of -> we log + fail-open.
    import retrieval_api.util.language_detect as mod

    fake = MagicMock()
    fake.detect_language_of.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mod, "_lingua_detector", fake)
    sample = "Long enough English sample, definitely not short."
    assert mod.detect_language(sample) == UNKNOWN_LANGUAGE


# -- language_correctness ----------------------------------------------------


@pytest.mark.parametrize(
    ("query_lang", "response_lang", "expected"),
    [
        ("nl", "nl", True),
        ("en", "en", True),
        ("de", "de", True),
        ("fr", "fr", True),
        ("pt", "pt", True),
        ("es", "es", True),
        ("nl", "en", False),
        ("de", "fr", False),
        ("es", "pt", False),
        # UNKNOWN on either side -> None (excluded from aggregates)
        (UNKNOWN_LANGUAGE, "en", None),
        ("en", UNKNOWN_LANGUAGE, None),
        (UNKNOWN_LANGUAGE, UNKNOWN_LANGUAGE, None),
    ],
)
def test_language_correctness_matches_truth_table(query_lang, response_lang, expected):
    assert language_correctness(query_lang, response_lang) is expected
