"""Language detection wrapper for chat synthesis observability.

SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07: passive language detection on the
user query and the model response, used for the
``chat_synthesis_complete`` log event. The detection is **observability
only** — never used to change synthesis behaviour. The system prompt
itself instructs the LLM to respond in the user's language; this module
exists so we can MEASURE whether that's actually happening.

Lingua is a pure-Python detector with deterministic output and no
external calls. We restrict the detector to the six target languages
plus a few European fallbacks so non-target languages get classified
into a known bucket instead of polluting the logs with random codes.

Knowledge-ingest already uses lingua for per-document detection in
``knowledge_ingest/contextual.py``. This module mirrors that pattern but
is owned by retrieval-api so the chat path doesn't depend on the ingest
package.
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)


# Six target languages + a few neighbours so that e.g. Italian doesn't
# misclassify as Spanish. Adding languages later means rebuilding the
# detector singleton; cheap.
_TARGET_LANGUAGES: Final[set[str]] = {"nl", "en", "de", "fr", "pt", "es"}

# Lingua needs a few characters before its detection is meaningful.
_MIN_CHARS: Final[int] = 30
_SAMPLE_CHARS: Final[int] = 500

# Default returned when the input is too short or the detector is
# unavailable. "und" matches the IANA "undetermined" code so consumers
# can filter it out cleanly without confusing it with a real language.
UNKNOWN_LANGUAGE: Final[str] = "und"


_lingua_detector: object | None = None


def _get_lingua_detector() -> object | None:
    """Return a cached LanguageDetector covering the six target languages
    plus a small set of European neighbours. Built lazily so importing
    this module stays cheap.
    """
    global _lingua_detector
    if _lingua_detector is not None:
        return _lingua_detector
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError:
        logger.warning(
            "language_detect_lingua_unavailable",
            extra={"reason": "lingua-language-detector not installed"},
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
            # European neighbours that often appear in mixed text — keep
            # these so they are correctly classified as "not a target"
            # rather than misattributed to a target language.
            Language.ITALIAN,
        )
        .with_preloaded_language_models()
        .build()
    )
    _lingua_detector = detector
    return detector


def detect_language(text: str) -> str:
    """Return ISO-639-1 code for the dominant language of ``text``.

    Returns :data:`UNKNOWN_LANGUAGE` ("und") when the text is too short
    to detect reliably, the detector is unavailable, or the detected
    language is outside :data:`_TARGET_LANGUAGES`.

    The detector is fail-open: any exception is logged at warning level
    and ``UNKNOWN_LANGUAGE`` is returned. Callers MUST treat
    ``UNKNOWN_LANGUAGE`` as "we don't know" — never as a real language.
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
    counting them as failures. A short user message
    ("ok", "thanks") will produce ``UNKNOWN_LANGUAGE`` for the query
    and the answer can still be in any language; we don't penalise that.

    Returns ``True`` when both detected languages match (and are known).
    Returns ``False`` when both are known but differ.
    """
    if query_language == UNKNOWN_LANGUAGE or response_language == UNKNOWN_LANGUAGE:
        return None
    return query_language == response_language
