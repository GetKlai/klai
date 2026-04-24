"""Text source extractor (SPEC-KB-SOURCES-001 Module 4).

Pure function: validate + normalise user-supplied text, derive a title,
and produce a deterministic source_ref so repeat submissions dedup
against the existing chunk store.
"""

from __future__ import annotations

import hashlib
import re

from app.services.source_extractors.exceptions import InvalidContentError

# Upper bound matches IngestRequest.content max_length on knowledge-ingest.
_MAX_TEXT_LEN = 500_000

# Any run of Unicode whitespace (spaces, tabs, newlines) is collapsed to a
# single ASCII space. Matches Python's default \s (re.UNICODE).
_WHITESPACE_RUN = re.compile(r"\s+")

_DEFAULT_TITLE = "Untitled note"
_TITLE_MAX_CHARS = 120


def extract_text(title: str | None, content: str) -> tuple[str, str, str]:
    """Normalise raw text for ingest.

    Returns a tuple of (title, normalised_content, source_ref) where:
      - title: explicit (trimmed) > first non-empty line of original content
        (<= 120 chars) > "Untitled note".
      - normalised_content: NUL bytes stripped, whitespace runs collapsed to
        a single space, leading/trailing whitespace stripped.
      - source_ref: f"text:sha256:{hex}" over the normalised content. The
        user-supplied title is NOT part of the hash — re-submitting the same
        paragraph with a different title is still a dedup hit.

    Raises InvalidContentError when content is not a string, exceeds the
    500,000-character limit, or normalises to empty.
    """
    if not isinstance(content, str):
        raise InvalidContentError("Content must be a string")
    if len(content) > _MAX_TEXT_LEN:
        raise InvalidContentError(f"Content exceeds {_MAX_TEXT_LEN:_} characters")

    # Fallback title source — take from ORIGINAL input (pre-collapse) so
    # line boundaries are still meaningful. This is the only place where
    # the original formatting matters.
    fallback_title = ""
    for raw_line in content.splitlines():
        stripped = raw_line.replace("\x00", "").strip()
        if stripped:
            fallback_title = stripped[:_TITLE_MAX_CHARS]
            break

    # R4.2 normalisation for storage + hash.
    cleaned = content.replace("\x00", "")
    normalised = _WHITESPACE_RUN.sub(" ", cleaned).strip()

    if not normalised:
        raise InvalidContentError("Content is empty after normalisation")

    # R4.3 title derivation.
    explicit = (title or "").strip()
    if explicit:
        final_title = explicit
    elif fallback_title:
        final_title = fallback_title
    else:
        final_title = _DEFAULT_TITLE

    # R4.4 deterministic source_ref.
    sha = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    source_ref = f"text:sha256:{sha}"

    return final_title, normalised, source_ref
