"""File-upload validation service.

SPEC-KB-FILE-UPLOAD-001 — validates a multipart upload and classifies
it into one of three pipelines:

- **text** — ``.md / .txt / .csv``: decode UTF-8 (with cp1252 fallback)
  and forward to ``/ingest/v1/document`` directly. Synchronous, fast,
  no docling involved.
- **docling** — ``.pdf / .docx / .pptx / .xlsx / .json / .xml``: stream
  to docling-serve's async queue. Returns a ``task_id`` that
  ``kb_upload_poller`` watches.
- **archive / .doc** — ``.zip / .tar / .doc``: not yet implemented.
  Surfaces as ``error_code: "phase_pending"`` in the per-file
  ``skipped[]`` array.

Magic-byte validation runs **before** any storage write or downstream
forward — a ``.exe`` renamed ``.pdf`` returns 400 ``mime_mismatch`` and
no docling submission is made. Filename sanitisation (path-traversal,
control chars, length cap) runs first so the filename that reaches
storage / logs / UI is always safe.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import filetype  # type: ignore[import-untyped]
from fastapi import HTTPException, status

# --- Format whitelists (by pipeline) ---------------------------------------

# Text path — decoded UTF-8 with cp1252 fallback, BOM stripped.
TEXT_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".csv"})

# Docling path — submitted to docling-serve /v1/convert/file/async.
# Each entry maps to a docling ``input_format`` and an authoritative
# magic-byte mime that ``filetype`` should report.
_DOCLING_FORMATS: dict[str, dict[str, str]] = {
    ".pdf": {"docling_format": "pdf", "expected_mime": "application/pdf"},
    ".docx": {
        "docling_format": "docx",
        "expected_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    ".xlsx": {
        "docling_format": "xlsx",
        "expected_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    ".pptx": {
        "docling_format": "pptx",
        "expected_mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
    # JSON / XML are text-bytes; we accept them on the docling path so
    # docling can detect ``json_docling`` / ``xml_jats`` / etc. via its
    # own format probe. Magic-byte validation skips these — they have
    # no binary signature.
    ".json": {"docling_format": "json", "expected_mime": ""},
    ".xml": {"docling_format": "xml", "expected_mime": ""},
}

DOCLING_EXTENSIONS: frozenset[str] = frozenset(_DOCLING_FORMATS)

# Archive path — extracted via ``app.services.archive`` with sunzip-style
# guards (compression-ratio cap, per-entry size, path-traversal, no nested
# archives, no symlinks). Each safe member is then dispatched through the
# matching text or docling pipeline.
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".zip", ".tar"})

# Pending — recognised per the SPEC's full whitelist but not yet
# implemented. ``.doc`` needs the libreoffice-headless sidecar.
PENDING_EXTENSIONS: frozenset[str] = frozenset({".doc"})

FULL_WHITELIST: frozenset[str] = TEXT_EXTENSIONS | DOCLING_EXTENSIONS | ARCHIVE_EXTENSIONS | PENDING_EXTENSIONS

# --- Size caps -------------------------------------------------------------

# Text path is read fully into memory (then decoded to a Python ``str``)
# before forwarding. 10 MB covers any realistic markdown / csv / txt.
MAX_TEXT_FILE_BYTES: int = 10 * 1024 * 1024

# Docling path streams to disk (UploadFile spool) before submission;
# 200 MB matches the Caddy edge cap on the upload route.
MAX_BINARY_FILE_BYTES: int = 200 * 1024 * 1024

# --- Filename sanitisation -------------------------------------------------

_UTF8_BOM = b"\xef\xbb\xbf"
_MAX_FILENAME_LENGTH = 255
_FILENAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]")


def sanitize_filename(raw: str | None) -> str:
    """Normalise an uploaded filename for safe persistence and display.

    Strips path components, control characters, and reserved-on-many-
    filesystems punctuation. Caps the length at
    :data:`_MAX_FILENAME_LENGTH` (preserving the extension) and falls
    back to ``"untitled"`` when the result is empty.
    """
    name = (raw or "").strip()
    if not name:
        return "untitled"

    name = PurePosixPath(name).name
    name = name.split("\\")[-1]
    name = _FILENAME_FORBIDDEN.sub("", name).strip()
    if not name:
        return "untitled"

    if len(name) <= _MAX_FILENAME_LENGTH:
        return name

    suffix = PurePosixPath(name).suffix
    stem_budget = max(0, _MAX_FILENAME_LENGTH - len(suffix))
    return name[:stem_budget] + suffix


def get_extension(filename: str) -> str:
    """Return the lowercase extension including dot. Empty string if none."""
    return PurePosixPath(filename).suffix.lower()


# --- Classification --------------------------------------------------------


def classify_extension(filename: str) -> tuple[str, str]:
    """Return ``(extension, pipeline)`` or raise HTTP 400.

    ``pipeline`` is one of:

    - ``"text"`` — caller decodes bytes and forwards via
      :func:`validate_text_upload`.
    - ``"docling"`` — caller passes binary content + mime to the docling
      path via :func:`validate_binary_upload`.
    - ``"archive"`` — caller extracts with ``app.services.archive`` and
      recurses each member through this dispatcher.
    - ``"phase_pending"`` — recognised format but not yet implemented;
      caller records the file as skipped.
    """
    ext = get_extension(filename)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "unsupported_extension", "filename": filename},
        )
    if ext in TEXT_EXTENSIONS:
        return ext, "text"
    if ext in DOCLING_EXTENSIONS:
        return ext, "docling"
    if ext in ARCHIVE_EXTENSIONS:
        return ext, "archive"
    if ext in PENDING_EXTENSIONS:
        return ext, "phase_pending"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error_code": "unsupported_extension",
            "filename": filename,
            "extension": ext,
        },
    )


def docling_format_for(extension: str) -> str:
    """Return the docling-serve ``input_format`` for a docling-path ext.

    Caller must ensure the extension was classified as ``"docling"``.
    """
    config = _DOCLING_FORMATS.get(extension)
    if config is None:  # pragma: no cover - guarded by classify_extension
        raise ValueError(f"docling_format_for called with non-docling ext: {extension!r}")
    return config["docling_format"]


# --- Text-path validation --------------------------------------------------


@dataclass(frozen=True)
class ValidatedTextFile:
    """Outcome of normalising a text-path upload."""

    filename: str
    extension: str
    content: str
    bytes_count: int
    source_ref: str
    title: str


def assert_size_within_text_cap(size_bytes: int) -> None:
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


def build_source_ref(content: str | bytes) -> str:
    """Build a content-addressed source_ref so re-uploads dedupe.

    Accepts text (``str``) or binary (``bytes``). The hash space is
    shared between paths — a file's source_ref is identical whether it
    was uploaded as ``.md`` text or ``.pdf`` binary.
    """
    if isinstance(content, str):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    else:
        digest = hashlib.sha256(content).hexdigest()
    return f"file:sha256:{digest}"


def derive_title(filename: str, extension: str) -> str:
    """Strip the extension from the filename to use as the artifact title."""
    base = filename.removesuffix(extension) if extension else filename
    base = base.strip()
    return base or "untitled"


def validate_text_upload(filename: str, raw: bytes) -> ValidatedTextFile:
    """Validate + normalise a text-path upload.

    Raises HTTP 400 / 413 on the first failing check.
    """
    ext, pipeline = classify_extension(filename)
    if pipeline != "text":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "wrong_pipeline_for_text",
                "filename": filename,
                "extension": ext,
                "pipeline": pipeline,
            },
        )
    assert_size_within_text_cap(len(raw))
    content = normalise_text_content(raw)
    if not content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "empty_content", "filename": filename},
        )
    return ValidatedTextFile(
        filename=filename,
        extension=ext,
        content=content,
        bytes_count=len(raw),
        source_ref=build_source_ref(content),
        title=derive_title(filename, ext),
    )


# --- Docling-path validation -----------------------------------------------


@dataclass(frozen=True)
class ValidatedBinaryFile:
    """Outcome of validating a docling-path upload.

    The binary content is kept as ``bytes`` because docling-serve takes
    multipart with the full body — there's no streaming opportunity
    once we've seen the bytes for hashing + magic-byte check.
    """

    filename: str
    extension: str
    docling_format: str
    mime: str
    content: bytes
    bytes_count: int
    source_ref: str
    title: str


def assert_size_within_binary_cap(size_bytes: int) -> None:
    if size_bytes > MAX_BINARY_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "file_too_large",
                "max_bytes": MAX_BINARY_FILE_BYTES,
                "actual_bytes": size_bytes,
            },
        )


def detect_mime(extension: str, raw: bytes) -> str:
    """Return the validated mime for a docling-path file or raise 400.

    For binary formats with magic bytes (``.pdf``, ``.docx``, ``.xlsx``,
    ``.pptx``) we check ``filetype.guess()`` against the format's
    expected mime. A mismatch raises 400 ``mime_mismatch``.

    For text-shaped formats (``.json``, ``.xml``) we skip magic-byte
    detection (none exists) and trust the extension — docling-serve
    runs its own format probe on submission.
    """
    config = _DOCLING_FORMATS.get(extension)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "unsupported_extension", "extension": extension},
        )

    expected = config["expected_mime"]
    if not expected:
        # Text-shaped; no magic bytes to check.
        return "text/plain"

    # Refuse empty bodies — magic-byte detection on zero bytes silently
    # returns None, which we'd otherwise misclassify as mime_mismatch.
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "empty_content", "extension": extension},
        )

    kind = filetype.guess(raw[:4096])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if kind is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "mime_mismatch",
                "extension": extension,
                "expected_mime": expected,
                "detected_mime": None,
            },
        )

    detected: str = kind.mime  # pyright: ignore[reportUnknownMemberType]
    if detected != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "mime_mismatch",
                "extension": extension,
                "expected_mime": expected,
                "detected_mime": detected,
            },
        )
    return detected


def validate_binary_upload(filename: str, raw: bytes) -> ValidatedBinaryFile:
    """Validate + classify a docling-path upload.

    Raises HTTP 400 on extension/mime mismatch, 413 on oversize.
    """
    ext, pipeline = classify_extension(filename)
    if pipeline != "docling":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "wrong_pipeline_for_binary",
                "filename": filename,
                "extension": ext,
                "pipeline": pipeline,
            },
        )
    assert_size_within_binary_cap(len(raw))
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "empty_content", "filename": filename},
        )
    mime = detect_mime(ext, raw)
    return ValidatedBinaryFile(
        filename=filename,
        extension=ext,
        docling_format=docling_format_for(ext),
        mime=mime,
        content=raw,
        bytes_count=len(raw),
        source_ref=build_source_ref(raw),
        title=derive_title(filename, ext),
    )
