"""Unit tests for app.utils.language_detect.

Mirrors retrieval-api's tests: per-text ISO-639-1 detection with
UNKNOWN_LANGUAGE fallback, and the language_correctness truth table.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.language_detect import (
    UNKNOWN_LANGUAGE,
    detect_language,
    language_correctness,
)


@pytest.mark.parametrize("text", ["", "  ", "short"])
def test_detect_language_returns_unknown_for_short_or_empty(text):
    assert detect_language(text) == UNKNOWN_LANGUAGE


def test_detect_language_dutch():
    text = "Hoe stel ik tweefactorauthenticatie in via de instellingen van de portal? Ik wil dit graag activeren."
    assert detect_language(text) == "nl"


def test_detect_language_english():
    text = (
        "How can I configure single sign-on with our identity provider? "
        "I want to make sure the redirect URIs are set correctly."
    )
    assert detect_language(text) == "en"


def test_detect_language_german():
    text = (
        "Wie konfiguriere ich Single Sign-On mit unserem Identitätsanbieter? "
        "Ich möchte sicherstellen, dass die URIs korrekt sind."
    )
    assert detect_language(text) == "de"


def test_detect_language_french():
    text = (
        "Comment puis-je configurer l'authentification unique avec notre "
        "fournisseur d'identité? Je veux m'assurer que les URI sont correctes."
    )
    assert detect_language(text) == "fr"


def test_detect_language_portuguese():
    text = (
        "Como posso configurar o single sign-on com o nosso provedor de "
        "identidade? Quero garantir que os URIs estão corretos."
    )
    assert detect_language(text) == "pt"


def test_detect_language_spanish():
    text = (
        "¿Cómo puedo configurar el inicio de sesión único con nuestro proveedor "
        "de identidad? Quiero asegurarme de que las URI sean correctas."
    )
    assert detect_language(text) == "es"


def test_detect_language_italian_falls_through_to_unknown():
    # Italian is in the detector's allow-list but is NOT a target language.
    text = (
        "Come posso configurare il single sign-on con il nostro provider "
        "di identità? Voglio essere sicuro che gli URI siano corretti."
    )
    assert detect_language(text) == UNKNOWN_LANGUAGE


def test_detect_language_returns_unknown_when_lingua_unavailable(monkeypatch):
    import app.utils.language_detect as mod

    monkeypatch.setattr(mod, "_lingua_detector", None)
    with patch.object(mod, "_get_lingua_detector", return_value=None):
        assert mod.detect_language("This is a sufficiently long English sample.") == UNKNOWN_LANGUAGE


def test_detect_language_returns_unknown_on_detector_exception(monkeypatch):
    import app.utils.language_detect as mod

    fake = MagicMock()
    fake.detect_language_of.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mod, "_lingua_detector", fake)
    assert mod.detect_language("Long enough English sample, definitely not short.") == UNKNOWN_LANGUAGE


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
        (UNKNOWN_LANGUAGE, "en", None),
        ("en", UNKNOWN_LANGUAGE, None),
        (UNKNOWN_LANGUAGE, UNKNOWN_LANGUAGE, None),
    ],
)
def test_language_correctness_truth_table(query_lang, response_lang, expected):
    assert language_correctness(query_lang, response_lang) is expected
