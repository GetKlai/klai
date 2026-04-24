"""Source extractors for URL, YouTube, and text ingest (SPEC-KB-SOURCES-001).

Each extractor is a pure async or sync function that takes user-supplied
input and returns a normalised (title, content) pair plus a deterministic
source_ref for dedup. HTTP / route layer concerns stay in the API.
"""

from __future__ import annotations

from app.services.source_extractors.exceptions import (
    InvalidContentError,
    InvalidUrlError,
    SourceExtractorError,
    SourceFetchError,
    SSRFBlockedError,
    UnsupportedSourceError,
)

__all__ = [
    "InvalidContentError",
    "InvalidUrlError",
    "SSRFBlockedError",
    "SourceExtractorError",
    "SourceFetchError",
    "UnsupportedSourceError",
]
