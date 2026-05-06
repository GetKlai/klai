"""Language detection wrapper for chat synthesis observability.

SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07: passive language detection on the
user query and the model response, used for the
``chat_synthesis_complete`` log event in partner_chat. The detection is
**observability only** — never used to change synthesis behaviour. The
actual chat behaviour is driven by the system prompt that lives in
``klai-chat-prompts.GROUNDED_CHAT_SYSTEM_PROMPT``.

This module is a near-clone of
``klai-retrieval-api/retrieval_api/util/language_detect.py``. The two
copies are intentional: each service depends only on its own utilities,
and a shared `klai-libs/language-detect` would be overkill for two
~80-line modules. If a third service needs the detector, that's the
moment to extract.
"""

from __future__ import annotations

from typing import Final

import structlog

logger = structlog.get_logger()


# Six target languages + a few European neighbours so that e.g. Italian
# doesn't misclassify as Spanish.
_TARGET_LANGUAGES: Final[set[str]] = {"nl", "en", "de", "fr", "pt", "es"}

_MIN_CHARS: Final[int] = 30
_SAMPLE_CHARS: Final[int] = 500

# Default returned when input is too short or detector unavailable.
UNKNOWN_LANGUAGE: Final[str] = "und"


_lingua_detector: object | None = None


def _get_lingua_detector() -> object | None:
    """Return a cached LanguageDetector instance. Built lazily."""
    global _lingua_detector
    if _lingua_detector is not None:
        return _lingua_detector
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError:
        logger.warning(
            "language_detect_lingua_unavailable",
            reason="lingua-language-detector not installed",
        )
        return None

    detector = (
        LanguageDetectorBuilder.from_languages(
            Language.DUTCH,
            Language.ENGLISH,
            Language.GERMAN,
            Language.FRENCH,
            Language.PORTUGUESE,
            Language.SPANISH,
            Language.ITALIAN,  # neighbour, classified as not-target
        )
        .with_preloaded_language_models()
        .build()
    )
    _lingua_detector = detector
    return detector


def detect_language(text: str) -> str:
    """Return ISO-639-1 code for the dominant language of ``text``.

    Returns :data:`UNKNOWN_LANGUAGE` ("und") when the text is too short,
    the detector is unavailable, or the detected language is outside
    the six target languages. Fail-open: exceptions log a warning and
    return ``UNKNOWN_LANGUAGE``.
    """
    if not text or len(text.strip()) < _MIN_CHARS:
        return UNKNOWN_LANGUAGE

    detector = _get_lingua_detector()
    if detector is None:
        return UNKNOWN_LANGUAGE

    sample = text.strip().replace("\n", " ")[:_SAMPLE_CHARS]
    try:
        result = detector.detect_language_of(sample)
    except Exception:
        logger.warning("language_detect_failed", exc_info=True)
        return UNKNOWN_LANGUAGE

    if result is None:
        return UNKNOWN_LANGUAGE
    iso = result.iso_code_639_1.name.lower()
    if iso in _TARGET_LANGUAGES:
        return iso
    return UNKNOWN_LANGUAGE


def language_correctness(query_language: str, response_language: str) -> bool | None:
    """Return whether response language matches query language.

    Returns ``None`` when either side is :data:`UNKNOWN_LANGUAGE` —
    callers should skip such samples in aggregate metrics rather than
    counting them as failures.
    """
    if query_language == UNKNOWN_LANGUAGE or response_language == UNKNOWN_LANGUAGE:
        return None
    return query_language == response_language
