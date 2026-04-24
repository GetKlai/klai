"""Typed exceptions for source extractors.

SPEC-KB-SOURCES-001 D8: route handlers translate these to HTTP statuses.
Keeping the hierarchy shallow makes it easy to `except SourceExtractorError`
at the top level when behaviour is identical across types.
"""

from __future__ import annotations


class SourceExtractorError(Exception):
    """Base class for all source-extractor failures."""


class InvalidUrlError(SourceExtractorError):
    """The provided URL could not be parsed or recognised."""


class SSRFBlockedError(SourceExtractorError):
    """The URL resolves to a disallowed IP range (private, loopback, etc.)."""


class SourceFetchError(SourceExtractorError):
    """Upstream fetch failed (network error, non-2xx, empty body)."""


class UnsupportedSourceError(SourceExtractorError):
    """The source cannot be ingested (e.g. YouTube video without transcript)."""


class InvalidContentError(SourceExtractorError):
    """Supplied content failed validation (too long, empty, non-string)."""
