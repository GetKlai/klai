"""Safe archive extraction for ``.zip`` and ``.tar`` uploads.

SPEC-KB-FILE-UPLOAD-001 — treats archives as all-or-nothing batch
containers. Every member must pass preflight and streaming guards before
the caller dispatches the member bytes through the normal text / docling
pipelines.

Defenses (sunzip-style; see also CVE-2024-0450, GHSA-ffj4-jq7m-9g6v,
PEP 706):

- **Path traversal** — entry name MUST be a safe relative POSIX path.
  Reject ``..``, ``.``, empty path segments, absolute paths, backslash
  separators, Windows drive letters, NUL, leading slashes.
- **Nested archives** — entries with a recognised archive extension
  (``.zip`` / ``.tar``) reject the whole archive; we never recurse.
- **Symlinks / devices (tar)** — only ``REGTYPE`` / ``AREGTYPE``
  members are accepted. Anything else rejects the whole archive.
- **Per-entry uncompressed cap** — refuses any member whose declared
  uncompressed size exceeds :data:`MAX_PER_ENTRY_BYTES` (zip header)
  or that exceeds it during streaming read (tar — no header guarantee).
- **Compression ratio cap** — abort the entire archive if the running
  ``uncompressed_bytes / compressed_bytes`` ratio exceeds
  :data:`MAX_COMPRESSION_RATIO` after the first 1 MB of decompression
  output. Catches 42-KB → 4-GB zip bombs early.
- **Total uncompressed cap** — abort the entire archive when cumulative
  output exceeds :data:`MAX_TOTAL_UNCOMPRESSED_BYTES`.
- **Member count / complexity caps** — abort when member count,
  docling-heavy member count, or weighted complexity exceeds the caps.
- **Whitelist per entry** — only members whose extension is in
  :func:`is_extractable_extension` are extracted; unsupported members
  reject the whole archive.

The extractor never invokes ``zipfile.extract*`` / ``tarfile.extract*``.
It currently returns member bytes for the existing dispatcher contract;
the streaming guards still apply while reading each member.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

from fastapi import HTTPException, status

from app.services.file_upload import (
    ARCHIVE_EXTENSIONS,
    DOCLING_EXTENSIONS,
    PENDING_EXTENSIONS,
    TEXT_EXTENSIONS,
)

# --- Caps (sunzip-style guards) -------------------------------------------

MAX_ENTRIES: int = 200
MAX_DOCLING_ENTRIES: int = 10
MAX_COMPLEXITY_UNITS: int = 200
MAX_PER_ENTRY_BYTES: int = 50 * 1024 * 1024  # 50 MB per file in archive
MAX_TOTAL_UNCOMPRESSED_BYTES: int = 500 * 1024 * 1024  # 500 MB cumulative
MAX_COMPRESSION_RATIO: float = 10.0
_ONE_MB: int = 1024 * 1024
_TEN_MB: int = 10 * 1024 * 1024
# Below this many output bytes we don't trust the running ratio (tiny
# archives have small denominators that produce wild ratios).
_RATIO_GUARD_FLOOR_BYTES: int = 1 * 1024 * 1024

# Read in 64 KiB chunks while streaming — small enough that an oversize
# member is caught within one chunk past the cap.
_STREAM_CHUNK: int = 64 * 1024


# --- Public types ----------------------------------------------------------


@dataclass(frozen=True)
class ExtractedEntry:
    """One file extracted from an archive."""

    filename: str
    _content: IO[bytes]
    bytes_count: int

    @property
    def content(self) -> bytes:
        """Read staged member bytes for the existing upload dispatcher."""
        self._content.seek(0)
        return self._content.read()


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of one safe-extract pass."""

    extracted: list[ExtractedEntry]
    skipped: list[object]


class ArchiveAbort(HTTPException):
    """Raised when an archive-level guard fires (whole-request rejection).

    All archive validation is all-or-nothing: one bad member rejects the
    whole archive before downstream ingest/docling side effects start.
    """

    def __init__(self, error_code: str, **detail: object) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": error_code, **detail},
        )


# --- Name + extension validation ------------------------------------------


def _is_safe_member_name(name: str) -> bool:
    """Return True iff ``name`` is a safe relative POSIX path.

    Matches the safezip / Python-3.12-tarfile-data-filter rules.
    """
    if not name or "\x00" in name:
        return False
    # Reject absolute paths (POSIX or Windows), drive letters, UNC paths.
    if name.startswith(("/", "\\")):
        return False
    if "\\" in name:
        return False
    raw_parts = name.split("/")
    # PurePosixPath collapses ``.`` and duplicate separators, so inspect
    # raw path segments before normalising.
    if any(part in {"", ".", ".."} for part in raw_parts):
        return False
    # Reject Windows drive markers in any segment, e.g. C:/foo.md.
    if any(len(part) >= 2 and part[1] == ":" for part in raw_parts):
        return False
    if PurePosixPath(name).is_absolute():
        return False
    return True


def _extension_rejection_reason(filename: str) -> str | None:
    ext = PurePosixPath(filename).suffix.lower()
    if ext in ARCHIVE_EXTENSIONS:
        return "archive_nested"
    if ext in PENDING_EXTENSIONS:
        return "doc_format_not_yet_supported"
    if ext in TEXT_EXTENSIONS or ext in DOCLING_EXTENSIONS:
        return None
    return "unsupported_extension"


def is_extractable_extension(filename: str) -> bool:
    """Return True iff the archive entry's extension is in the
    text or docling whitelist (so the caller can dispatch it through
    a known pipeline).

    Nested archives (``.zip`` / ``.tar``) and ``.doc`` are excluded —
    archives because we never recurse, ``.doc`` because the libreoffice
    pipeline is a separate follow-up.
    """
    ext = PurePosixPath(filename).suffix.lower()
    return ext in TEXT_EXTENSIONS or ext in DOCLING_EXTENSIONS


def complexity_units(filename: str, declared_uncompressed_size: int) -> int:
    """Return weighted archive complexity units for one member."""
    ext = PurePosixPath(filename).suffix.lower()
    if ext in {".md", ".txt", ".csv"}:
        return 1 if declared_uncompressed_size <= _ONE_MB else 2
    if ext in {".json", ".xml"}:
        return 3
    if ext in {".docx", ".xlsx", ".pptx"}:
        return 5
    if ext == ".pdf":
        if declared_uncompressed_size <= _TEN_MB:
            return 10
        extra_bytes = declared_uncompressed_size - _TEN_MB
        return 10 + ((extra_bytes + _TEN_MB - 1) // _TEN_MB)
    return 0


def _preflight_member(
    *,
    name: str,
    declared_uncompressed_size: int,
    seen_names: set[str],
    totals: dict[str, int],
) -> None:
    if not _is_safe_member_name(name):
        raise ArchiveAbort("archive_path_traversal", filename=name or "<unnamed>")

    normalised = PurePosixPath(name).as_posix().lower()
    if normalised in seen_names:
        raise ArchiveAbort("archive_duplicate_entry", filename=name)
    seen_names.add(normalised)

    rejection = _extension_rejection_reason(name)
    if rejection is not None:
        raise ArchiveAbort(rejection, filename=name)

    if declared_uncompressed_size > MAX_PER_ENTRY_BYTES:
        raise ArchiveAbort(
            "archive_entry_too_large",
            filename=name,
            max_bytes=MAX_PER_ENTRY_BYTES,
            declared_bytes=declared_uncompressed_size,
        )

    totals["entries"] += 1
    if totals["entries"] > MAX_ENTRIES:
        raise ArchiveAbort("archive_too_many_entries", max_entries=MAX_ENTRIES)

    ext = PurePosixPath(name).suffix.lower()
    if ext in DOCLING_EXTENSIONS:
        totals["docling_entries"] += 1
        if totals["docling_entries"] > MAX_DOCLING_ENTRIES:
            raise ArchiveAbort(
                "archive_too_many_docling_entries",
                max_entries=MAX_DOCLING_ENTRIES,
            )

    totals["uncompressed"] += declared_uncompressed_size
    if totals["uncompressed"] > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ArchiveAbort(
            "archive_total_size",
            max_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES,
        )

    totals["complexity"] += complexity_units(name, declared_uncompressed_size)
    if totals["complexity"] > MAX_COMPLEXITY_UNITS:
        raise ArchiveAbort(
            "archive_complexity_budget_exceeded",
            max_units=MAX_COMPLEXITY_UNITS,
        )


# --- Archive type detection ------------------------------------------------


def detect_archive_type(filename: str) -> str:
    """Return ``"zip"`` or ``"tar"`` or raise ``ArchiveAbort``.

    Trusts the filename extension — magic-byte verification is the
    caller's job before this function runs.
    """
    ext = PurePosixPath(filename).suffix.lower()
    if ext == ".zip":
        return "zip"
    if ext == ".tar":
        return "tar"
    raise ArchiveAbort("unsupported_archive_type", extension=ext)


# --- Streaming readers -----------------------------------------------------


def _read_with_caps(
    source: IO[bytes],
    *,
    declared_compressed_size: int | None,
    cumulative_uncompressed: int,
) -> tuple[IO[bytes], int]:
    """Stream-read ``source`` enforcing per-entry + cumulative + ratio caps.

    Raises ``ArchiveAbort`` on any violation. Returns a disk-backed staged
    file plus byte count if every cap held.
    """
    output = tempfile.SpooledTemporaryFile(max_size=_ONE_MB)
    output_bytes = 0
    while True:
        chunk = source.read(_STREAM_CHUNK)
        if not chunk:
            break
        output.write(chunk)
        output_bytes += len(chunk)
        if output_bytes > MAX_PER_ENTRY_BYTES:
            raise ArchiveAbort(
                "archive_entry_too_large",
                max_bytes=MAX_PER_ENTRY_BYTES,
            )
        running_total = cumulative_uncompressed + output_bytes
        if running_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArchiveAbort(
                "archive_total_size",
                max_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES,
            )
        # Compression-ratio guard kicks in only after we have enough
        # bytes that the ratio is meaningful. Below the floor a small
        # entry can mathematically exceed 10:1 without being a bomb.
        if (
            declared_compressed_size is not None
            and declared_compressed_size > 0
            and output_bytes >= _RATIO_GUARD_FLOOR_BYTES
        ):
            ratio = output_bytes / declared_compressed_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise ArchiveAbort(
                    "archive_compression_ratio",
                    ratio=round(ratio, 2),
                    max_ratio=MAX_COMPRESSION_RATIO,
                )
    output.seek(0)
    return output, output_bytes


def _iter_zip(content: bytes) -> Iterator[ExtractedEntry]:
    """Yield ``ExtractedEntry`` for each zip member."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), mode="r")
    except zipfile.BadZipFile as exc:
        raise ArchiveAbort("archive_malformed", archive_type="zip") from exc

    members = [info for info in archive.infolist() if not info.is_dir()]
    totals = {"entries": 0, "docling_entries": 0, "uncompressed": 0, "complexity": 0}
    seen_names: set[str] = set()
    for info in members:
        _preflight_member(
            name=info.filename,
            declared_uncompressed_size=info.file_size,
            seen_names=seen_names,
            totals=totals,
        )

    cumulative = 0
    for info in members:
        name = info.filename
        with archive.open(info, mode="r") as fp:
            staged, bytes_count = _read_with_caps(
                fp,
                declared_compressed_size=info.compress_size,
                cumulative_uncompressed=cumulative,
            )
        cumulative += bytes_count
        yield ExtractedEntry(filename=name, _content=staged, bytes_count=bytes_count)


def _iter_tar(content: bytes) -> Iterator[ExtractedEntry]:
    """Yield entries for each tar member.

    Only ``REGTYPE`` / ``AREGTYPE`` members survive — symlinks, devices,
    fifos, hard links, and directories reject the archive.
    """
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:")
    except tarfile.TarError as exc:
        raise ArchiveAbort("archive_malformed", archive_type="tar") from exc

    members = [member for member in archive if not member.isdir()]
    totals = {"entries": 0, "docling_entries": 0, "uncompressed": 0, "complexity": 0}
    seen_names: set[str] = set()
    for member in members:
        if not member.isreg():
            raise ArchiveAbort("archive_unsafe_entry", filename=member.name)
        _preflight_member(
            name=member.name,
            declared_uncompressed_size=member.size,
            seen_names=seen_names,
            totals=totals,
        )

    cumulative = 0
    for member in members:
        name = member.name
        fp = archive.extractfile(member)
        if fp is None:
            raise ArchiveAbort("archive_unsafe_entry", filename=name)
        # tar lacks per-member compression metadata. We use the
        # declared uncompressed size as a sanity proxy — a tar can't
        # be zip-bombed in the classic sense, but an oversize member
        # still lands here.
        staged, bytes_count = _read_with_caps(
            fp,
            declared_compressed_size=None,
            cumulative_uncompressed=cumulative,
        )
        cumulative += bytes_count
        yield ExtractedEntry(filename=name, _content=staged, bytes_count=bytes_count)


# --- Public API ------------------------------------------------------------


def extract_archive(filename: str, content: bytes) -> ExtractionResult:
    """Safely extract one ``.zip`` / ``.tar`` archive.

    Returns the list of accepted entries. Whole-archive failures (unsafe
    member, zip bomb, malformed, too-many-entries, oversize entry,
    oversize cumulative) raise :class:`ArchiveAbort`.

    Raises:
        ArchiveAbort: on whole-archive guard violation.
    """
    archive_type = detect_archive_type(filename)
    extracted: list[ExtractedEntry] = []
    skipped: list[object] = []

    iterator = _iter_zip(content) if archive_type == "zip" else _iter_tar(content)
    for entry in iterator:
        extracted.append(entry)

    return ExtractionResult(extracted=extracted, skipped=skipped)
