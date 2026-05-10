"""Safe archive extraction for ``.zip`` and ``.tar`` uploads.

SPEC-KB-FILE-UPLOAD-001 — adds the archive pipeline. Extracts each
member into memory under a strict guard set and yields
``(filename, bytes)`` pairs that the caller dispatches through the
normal text / docling pipelines.

Defenses (sunzip-style; see also CVE-2024-0450, GHSA-ffj4-jq7m-9g6v,
PEP 706):

- **Path traversal** — entry name MUST be a single basename. Reject
  ``..``, absolute paths, backslash separators, NUL, leading slashes.
- **Nested archives** — entries with a recognised archive extension
  (``.zip`` / ``.tar``) are rejected; we never recurse.
- **Symlinks / devices (tar)** — only ``REGTYPE`` / ``AREGTYPE``
  members are considered. Anything else is dropped silently.
- **Per-entry uncompressed cap** — refuses any member whose declared
  uncompressed size exceeds :data:`MAX_PER_ENTRY_BYTES` (zip header)
  or that exceeds it during streaming read (tar — no header guarantee).
- **Compression ratio cap** — abort the entire archive if the running
  ``uncompressed_bytes / compressed_bytes`` ratio exceeds
  :data:`MAX_COMPRESSION_RATIO` after the first 1 MB of decompression
  output. Catches 42-KB → 4-GB zip bombs early.
- **Total uncompressed cap** — abort the entire archive when cumulative
  output exceeds :data:`MAX_TOTAL_UNCOMPRESSED_BYTES`.
- **Member count cap** — abort when the archive header reports more
  than :data:`MAX_ENTRIES` entries.
- **Whitelist per entry** — only members whose extension is in
  :func:`is_extractable_extension` are extracted; others are recorded
  as skipped with reason ``archive_unsafe_entry``.

The extractor is a pure generator: it never writes to the filesystem
and never invokes ``zipfile.extract*``. The caller persists the bytes
via the same pipelines a direct upload would use.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

from fastapi import HTTPException, status

from app.services.file_upload import DOCLING_EXTENSIONS, TEXT_EXTENSIONS

# --- Caps (sunzip-style guards) -------------------------------------------

MAX_ENTRIES: int = 50
MAX_PER_ENTRY_BYTES: int = 50 * 1024 * 1024  # 50 MB per file in archive
MAX_TOTAL_UNCOMPRESSED_BYTES: int = 500 * 1024 * 1024  # 500 MB cumulative
MAX_COMPRESSION_RATIO: float = 10.0
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
    content: bytes
    bytes_count: int


@dataclass(frozen=True)
class SkippedEntry:
    """One member that was rejected during extraction."""

    filename: str
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of one safe-extract pass."""

    extracted: list[ExtractedEntry]
    skipped: list[SkippedEntry]


class ArchiveAbort(HTTPException):
    """Raised when an archive-level guard fires (whole-request rejection).

    Per-entry rejections are recorded in ``skipped[]`` instead — only
    archive-wide failures (zip bomb, oversize total, too many entries,
    malformed archive) raise.
    """

    def __init__(self, error_code: str, **detail: object) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": error_code, **detail},
        )


# --- Name + extension validation ------------------------------------------


def _is_safe_member_name(name: str) -> bool:
    """Return True iff ``name`` is a single basename (no traversal).

    Matches the safezip / Python-3.12-tarfile-data-filter rules.
    """
    if not name or "\x00" in name:
        return False
    # PurePosixPath collapses ``"."`` and ``"./"`` to empty parts, so we
    # also reject the bare strings explicitly.
    if name in {".", ".."}:
        return False
    # Reject absolute paths (POSIX or Windows), drive letters, UNC paths.
    if name.startswith(("/", "\\")):
        return False
    if len(name) >= 2 and name[1] == ":":
        return False
    # Reject path separators — must be a basename.
    if "/" in name or "\\" in name:
        return False
    # Reject parent-traversal markers.
    parts = PurePosixPath(name).parts
    if any(p in {"..", "."} for p in parts):
        return False
    return True


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
) -> bytes:
    """Stream-read ``source`` enforcing per-entry + cumulative + ratio caps.

    Raises ``ArchiveAbort`` on any violation. Returns the full content
    bytes if every cap held.
    """
    output = bytearray()
    while True:
        chunk = source.read(_STREAM_CHUNK)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > MAX_PER_ENTRY_BYTES:
            raise ArchiveAbort(
                "archive_entry_too_large",
                max_bytes=MAX_PER_ENTRY_BYTES,
            )
        running_total = cumulative_uncompressed + len(output)
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
            and len(output) >= _RATIO_GUARD_FLOOR_BYTES
        ):
            ratio = len(output) / declared_compressed_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise ArchiveAbort(
                    "archive_compression_ratio",
                    ratio=round(ratio, 2),
                    max_ratio=MAX_COMPRESSION_RATIO,
                )
    return bytes(output)


def _iter_zip(content: bytes) -> Iterator[ExtractedEntry | SkippedEntry]:
    """Yield ``ExtractedEntry`` / ``SkippedEntry`` for each zip member.

    Caller is responsible for the cumulative ``uncompressed`` byte
    counter and bailing out on whole-archive failures.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), mode="r")
    except zipfile.BadZipFile as exc:
        raise ArchiveAbort("archive_malformed", archive_type="zip") from exc

    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise ArchiveAbort("archive_too_many_entries", max_entries=MAX_ENTRIES, received=len(members))

    cumulative = 0
    for info in members:
        if info.is_dir():
            continue
        name = info.filename
        if not _is_safe_member_name(name):
            yield SkippedEntry(filename=name or "<unnamed>", reason="archive_path_traversal")
            continue
        if not is_extractable_extension(name):
            yield SkippedEntry(filename=name, reason="archive_unsafe_entry")
            continue
        # The zip header tells us the declared uncompressed size. Refuse
        # over-sized entries upfront — saves the streaming read.
        if info.file_size > MAX_PER_ENTRY_BYTES:
            raise ArchiveAbort(
                "archive_entry_too_large",
                filename=name,
                max_bytes=MAX_PER_ENTRY_BYTES,
                declared_bytes=info.file_size,
            )
        with archive.open(info, mode="r") as fp:
            data = _read_with_caps(
                fp,
                declared_compressed_size=info.compress_size,
                cumulative_uncompressed=cumulative,
            )
        cumulative += len(data)
        yield ExtractedEntry(filename=name, content=data, bytes_count=len(data))


def _iter_tar(content: bytes) -> Iterator[ExtractedEntry | SkippedEntry]:
    """Yield entries for each tar member.

    Only ``REGTYPE`` / ``AREGTYPE`` members survive — symlinks, devices,
    fifos, hard links, and directories are dropped silently. The caller
    treats nothing-extracted as a hard rejection at the route level.
    """
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:")
    except tarfile.TarError as exc:
        raise ArchiveAbort("archive_malformed", archive_type="tar") from exc

    cumulative = 0
    member_count = 0
    for member in archive:
        if member.isdir():
            continue
        member_count += 1
        if member_count > MAX_ENTRIES:
            raise ArchiveAbort("archive_too_many_entries", max_entries=MAX_ENTRIES)
        # Only regular files. Drop symlinks, hard links, devices, fifos.
        if not member.isreg():
            yield SkippedEntry(filename=member.name, reason="archive_unsafe_entry")
            continue
        name = member.name
        if not _is_safe_member_name(name):
            yield SkippedEntry(filename=name or "<unnamed>", reason="archive_path_traversal")
            continue
        if not is_extractable_extension(name):
            yield SkippedEntry(filename=name, reason="archive_unsafe_entry")
            continue
        if member.size > MAX_PER_ENTRY_BYTES:
            raise ArchiveAbort(
                "archive_entry_too_large",
                filename=name,
                max_bytes=MAX_PER_ENTRY_BYTES,
                declared_bytes=member.size,
            )
        fp = archive.extractfile(member)
        if fp is None:
            yield SkippedEntry(filename=name, reason="archive_unsafe_entry")
            continue
        # tar lacks per-member compression metadata. We use the
        # declared uncompressed size as a sanity proxy — a tar can't
        # be zip-bombed in the classic sense, but an oversize member
        # still lands here.
        data = _read_with_caps(fp, declared_compressed_size=None, cumulative_uncompressed=cumulative)
        cumulative += len(data)
        yield ExtractedEntry(filename=name, content=data, bytes_count=len(data))


# --- Public API ------------------------------------------------------------


def extract_archive(filename: str, content: bytes) -> ExtractionResult:
    """Safely extract one ``.zip`` / ``.tar`` archive.

    Returns the list of accepted entries plus the per-member rejection
    list. Whole-archive failures (zip bomb, malformed, too-many-entries,
    oversize entry, oversize cumulative) raise :class:`ArchiveAbort`.

    Raises:
        ArchiveAbort: on whole-archive guard violation.
    """
    archive_type = detect_archive_type(filename)
    extracted: list[ExtractedEntry] = []
    skipped: list[SkippedEntry] = []

    iterator = _iter_zip(content) if archive_type == "zip" else _iter_tar(content)
    for entry in iterator:
        if isinstance(entry, ExtractedEntry):
            extracted.append(entry)
        else:
            skipped.append(entry)

    return ExtractionResult(extracted=extracted, skipped=skipped)
