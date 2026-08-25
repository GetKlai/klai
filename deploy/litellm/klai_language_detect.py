"""Stdlib-only language detection helpers for LiteLLM hook modules.

The litellm container runs the STOCK ghcr.io/berriai/litellm image with
individual .py files bind-mounted onto PYTHONPATH — no Dockerfile, no
pip-install step. The lingua-backed canonical detector in
klai-retrieval-api therefore cannot be imported here; this module is the
deliberate stdlib approximation over the same six target languages,
shared by klai_pii_observe (telemetry) and klai_kb_system_prompt
(deterministic response-language injection).
"""

from __future__ import annotations

import re

from klai_kb_request_context import message_text as _message_text

TARGET_LANGUAGES = ("nl", "en", "de", "fr", "pt", "es")
UNKNOWN_LANGUAGE = "und"
LANGUAGE_NAMES = {
    "nl": "Dutch",
    "en": "English",
    "de": "German",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
}

# Lingua needs ~30 chars before it trusts its own result; mirrored here so
# short/greeting-only turns consistently report "und" rather than a guess.
_MIN_CHARS_FOR_DETECTION = 30
_MIN_STOPWORD_HITS = 2

_STOPWORDS: dict[str, frozenset[str]] = {
    "nl": frozenset(
        {
            "de", "het", "een", "en", "van", "ik", "je", "is", "dat", "niet",
            "met", "voor", "op", "aan", "te", "dit", "ook", "zijn", "wij",
            "hebben", "kunt", "graag", "alstublieft",
        }
    ),
    "en": frozenset(
        {
            "the", "and", "is", "of", "to", "in", "that", "it", "for",
            "with", "on", "as", "are", "was", "this", "have", "you", "not",
            "please", "could", "would",
        }
    ),
    "de": frozenset(
        {
            "der", "die", "das", "und", "ist", "nicht", "mit", "für", "auf",
            "den", "dem", "des", "ein", "eine", "sie", "wir", "haben",
            "bitte", "können",
        }
    ),
    "fr": frozenset(
        {
            "le", "la", "les", "et", "est", "un", "une", "pour", "avec",
            "sur", "dans", "ne", "pas", "je", "vous", "nous", "des",
            "merci", "pouvez",
        }
    ),
    "pt": frozenset(
        {
            "o", "a", "os", "as", "de", "e", "um", "uma", "para", "com",
            "não", "em", "que", "você", "nós", "é", "por", "favor",
        }
    ),
    "es": frozenset(
        {
            "el", "la", "los", "las", "de", "y", "un", "una", "para",
            "con", "no", "en", "que", "usted", "nosotros", "es", "por",
            "favor",
        }
    ),
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str) -> str:
    """Return a best-effort ISO-639-1-ish code, or ``UNKNOWN_LANGUAGE``."""
    if not text or len(text.strip()) < _MIN_CHARS_FOR_DETECTION:
        return UNKNOWN_LANGUAGE

    tokens = [tok.lower() for tok in _WORD_RE.findall(text)]
    if not tokens:
        return UNKNOWN_LANGUAGE

    scores = {
        lang: sum(1 for tok in tokens if tok in words)
        for lang, words in _STOPWORDS.items()
    }
    best_score = max(scores.values())
    if best_score < _MIN_STOPWORD_HITS:
        return UNKNOWN_LANGUAGE

    # A single ambiguous overlap word ("de" is a stopword in nl/fr/pt/es)
    # must not silently pick a winner - report unknown rather than guess.
    tied = [lang for lang, score in scores.items() if score == best_score]
    if len(tied) > 1:
        return UNKNOWN_LANGUAGE
    return tied[0]


def latest_substantive_user_text(messages: list[dict]) -> str:
    """Return the latest user turn with at least five words, else the first."""
    oldest_user_text: str | None = None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _message_text(message)
        if oldest_user_text is None:
            oldest_user_text = text

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _message_text(message)
        if len(_WORD_RE.findall(text)) >= 5:
            return text

    return oldest_user_text or ""


def detect_response_language(messages: list[dict]) -> str:
    return detect_language(latest_substantive_user_text(messages))
