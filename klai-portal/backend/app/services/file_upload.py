"""File-upload validation service.

SPEC-KB-FILE-UPLOAD-001 Phase 1A: text-path only (`.md`, `.txt`, `.csv`).

The full SPEC enumerates 12 accepted extensions (REQ-1). Phase 1A handles
the three pure-text formats by routing them through the **existing**
``/ingest/v1/document`` text-ingest pipeline. Binary formats (`.pdf`,
`.docx`, `.pptx`, `.xlsx`, `.json`, `.xml`) are recognised here but
returned with ``error_code: "phase_pending"`` so the UI can show a
clear "binnenkort" message instead of the previous 500. Archive formats
(`.zip`, `.tar`) follow the same pattern. `.doc` is the Phase-4 LibreOffice
target.

Garage S3 + dedicated ``/ingest/v1/file`` endpoint land in Phase 1B.
This module deliberately has **no** S3 client and **no** docling adapter
yet — those are scoped out per SPEC §7 phasing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import HTTPException, status

# Phase 1A whitelist — extensions that route via the existing text-ingest
# pipeline today. Adding any extension here must be paired with a
# normalise / decode path; binary formats belong in Phase 1B+ instead.
PHASE_1_TEXT_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".csv"})

# Full whitelist per SPEC REQ-1. Anything in this set but not in
# PHASE_1_TEXT_EXTENSIONS returns ``phase_pending`` so the UI can map
# to a localised "wordt binnenkort ondersteund" message.
FULL_WHITELIST: frozenset[str] = frozenset(
    {
        ".csv",
        ".doc",
        ".docx",
        ".json",
        ".md",
        ".pdf",
        ".pptx",
        ".tar",
        ".txt",
        ".xlsx",
        ".xml",
        ".zip",
    }
)

# Per-file size cap for the text path. Phase 1A reads the entire body into
# memory before forwarding to the existing text-ingest path; the Phase 1B
# streaming path raises this to the SPEC's 200 MB. 10 MB covers any
# realistic markdown/csv/txt while keeping portal-api RSS bounded.
MAX_TEXT_FILE_BYTES: int = 10 * 1024 * 1024

# UTF-8 BOM that Excel and some Windows tools prepend to CSV.
_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class ValidatedTextFile:
    """Outcome of normalising a text-path upload.

    ``content`` is decoded UTF-8 with BOM stripped. ``source_ref`` is a
    content-addressed identifier so re-uploads of the same bytes dedupe
    via knowledge-ingest's existing path-keyed insert.
    """

    filename: str
    extension: str
    content: str
    bytes_count: int
    source_ref: str
    title: str


def get_extension(filename: str) -> str:
    """Return the lowercase extension including dot.

    ``"Foo.PDF"`` → ``".pdf"``. Empty string when the filename has no
    extension (rejected by :func:`classify_extension`).
    """
    return PurePosixPath(filename).suffix.lower()


def classify_extension(filename: str) -> tuple[str, str]:
    """Return ``(extension, phase)`` or raise HTTP 400.

    ``phase`` is one of:

    - ``"phase1"`` — extension is in the Phase 1A text whitelist; the
      caller should read + decode the body and forward to
      ``/ingest/v1/document``.
    - ``"phase_pending"`` — extension is recognised per the SPEC's full
      list but the binary/archive path is not yet implemented; the caller
      should record the file as skipped with reason ``phase_pending``.

    Raises ``HTTPException 400 unsupported_extension`` when the
    extension is not in the SPEC whitelist at all.
    """
    ext = get_extension(filename)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "unsupported_extension",
                "filename": filename,
            },
        )
    if ext in PHASE_1_TEXT_EXTENSIONS:
        return ext, "phase1"
    if ext in FULL_WHITELIST:
        return ext, "phase_pending"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error_code": "unsupported_extension",
            "filename": filename,
            "extension": ext,
        },
    )


def assert_size_within_text_cap(size_bytes: int) -> None:
    """Reject a Phase-1A text-path upload that exceeds the in-memory cap."""
    if size_bytes > MAX_TEXT_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "file_too_large",
                "max_bytes": MAX_TEXT_FILE_BYTES,
                "actual_bytes": size_bytes,
            },
        )


def normalise_text_content(raw: bytes) -> str:
    """Decode upload bytes to a UTF-8 ``str``, BOM stripped.

    UTF-8 is attempted first. cp1252 is the fallback so the common
    Excel-CSV-on-Windows case still imports successfully (R-12 in the
    SPEC). Anything that decodes neither raises HTTP 400 with
    ``error_code: "invalid_text_encoding"``.
    """
    if raw.startswith(_UTF8_BOM):
        raw = raw[len(_UTF8_BOM) :]

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "invalid_text_encoding",
                "tried_encodings": ["utf-8", "cp1252"],
            },
        ) from exc


def build_source_ref(content: str) -> str:
    """Build a content-addressed source_ref so re-uploads dedupe."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"file:sha256:{digest}"


def derive_title(filename: str, extension: str) -> str:
    """Strip the extension from the filename to use as the artifact title.

    A bare ``"chemie"`` (no extension) round-trips unchanged. An empty
    filename falls back to ``"untitled"`` so the artifact always has
    a non-empty display name.
    """
    base = filename.removesuffix(extension) if extension else filename
    base = base.strip()
    return base or "untitled"


def validate_text_upload(filename: str, raw: bytes) -> ValidatedTextFile:
    """End-to-end validation for a Phase-1A text-path upload.

    Combines :func:`classify_extension`, :func:`assert_size_within_text_cap`,
    :func:`normalise_text_content`, :func:`build_source_ref` and
    :func:`derive_title`. Raises HTTP 400 / 413 on the first failing
    check (caller decides whether to abort the whole request or skip the
    individual file).
    """
    ext, phase = classify_extension(filename)
    if phase != "phase1":
        # Caller is responsible for routing phase_pending — surface it
        # explicitly rather than silently falling through.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "phase_pending",
                "filename": filename,
                "extension": ext,
            },
        )
    assert_size_within_text_cap(len(raw))
    content = normalise_text_content(raw)
    if not content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "empty_content",
                "filename": filename,
            },
        )
    return ValidatedTextFile(
        filename=filename,
        extension=ext,
        content=content,
        bytes_count=len(raw),
        source_ref=build_source_ref(content),
        title=derive_title(filename, ext),
    )
