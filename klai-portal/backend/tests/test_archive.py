"""Unit tests for app.services.archive (SPEC-KB-FILE-UPLOAD-001).

The extractor is a stdlib-only module that wraps ``zipfile`` /
``tarfile`` with sunzip-style guards. Tests cover the canonical zip-
bomb, path-traversal, nested-archive, oversize-entry, and
unsafe-member shapes.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from app.services import archive

# --- Helpers ---------------------------------------------------------------


def _build_zip(members: list[tuple[str, bytes]]) -> bytes:
    """Build a zip archive in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue()


def _build_tar(members: list[tuple[str, bytes]]) -> bytes:
    """Build a tar archive in memory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.type = tarfile.REGTYPE
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# --- Name validation -------------------------------------------------------


class TestSafeMemberName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("notes.md", True),
            ("Chemie 4VWO.pdf", True),
            ("../../etc/passwd", False),
            ("/etc/passwd", False),
            ("..", False),
            (".", False),
            ("a/b.md", False),
            ("a\\b.md", False),
            ("C:\\foo.md", False),
            ("hello\x00.md", False),
            ("", False),
        ],
    )
    def test_known_attacks(self, name: str, expected: bool) -> None:
        assert archive._is_safe_member_name(name) is expected


class TestExtractableExtension:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("notes.md", True),
            ("doc.pdf", True),
            ("data.csv", True),
            ("page.html", False),
            ("inner.zip", False),
            ("inner.tar", False),
            ("legacy.doc", False),
            ("script.exe", False),
        ],
    )
    def test_whitelist(self, name: str, expected: bool) -> None:
        assert archive.is_extractable_extension(name) is expected


# --- Detect type -----------------------------------------------------------


class TestDetectArchiveType:
    def test_zip(self) -> None:
        assert archive.detect_archive_type("foo.zip") == "zip"

    def test_tar(self) -> None:
        assert archive.detect_archive_type("foo.tar") == "tar"

    def test_unknown_raises(self) -> None:
        with pytest.raises(archive.ArchiveAbort) as excinfo:
            archive.detect_archive_type("foo.rar")
        assert excinfo.value.detail["error_code"] == "unsupported_archive_type"


# --- Zip extraction --------------------------------------------------------


class TestZipExtraction:
    def test_happy_path_text_members(self) -> None:
        zip_bytes = _build_zip([("notes.md", b"# Hello"), ("data.csv", b"a,b\n1,2\n")])
        result = archive.extract_archive("bundle.zip", zip_bytes)

        assert len(result.extracted) == 2
        names = {e.filename for e in result.extracted}
        assert names == {"notes.md", "data.csv"}
        assert result.skipped == []

    def test_path_traversal_skipped(self) -> None:
        zip_bytes = _build_zip([("notes.md", b"# ok"), ("../../etc/passwd.md", b"# attack")])
        result = archive.extract_archive("bundle.zip", zip_bytes)

        assert len(result.extracted) == 1
        assert result.extracted[0].filename == "notes.md"
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == "archive_path_traversal"

    def test_nested_archive_skipped(self) -> None:
        zip_bytes = _build_zip([("notes.md", b"# ok"), ("inner.zip", b"PK\x03\x04")])
        result = archive.extract_archive("bundle.zip", zip_bytes)

        assert len(result.extracted) == 1
        assert result.extracted[0].filename == "notes.md"
        assert any(s.reason == "archive_unsafe_entry" for s in result.skipped)

    def test_unknown_extension_skipped(self) -> None:
        zip_bytes = _build_zip([("notes.md", b"# ok"), ("script.sh", b"#!/bin/sh")])
        result = archive.extract_archive("bundle.zip", zip_bytes)

        assert len(result.extracted) == 1
        assert result.extracted[0].filename == "notes.md"
        assert any(s.reason == "archive_unsafe_entry" for s in result.skipped)

    def test_too_many_entries_aborts(self) -> None:
        members = [(f"f{i}.md", b"x") for i in range(archive.MAX_ENTRIES + 1)]
        zip_bytes = _build_zip(members)

        with pytest.raises(archive.ArchiveAbort) as excinfo:
            archive.extract_archive("big.zip", zip_bytes)
        assert excinfo.value.detail["error_code"] == "archive_too_many_entries"

    def test_oversize_entry_header_aborts(self) -> None:
        # Build a member whose declared file_size > cap. Easiest: make
        # a real big member; we use exactly cap+1 of zeros (compresses
        # well, header still records uncompressed size).
        big = b"\x00" * (archive.MAX_PER_ENTRY_BYTES + 1)
        zip_bytes = _build_zip([("big.md", big)])

        with pytest.raises(archive.ArchiveAbort) as excinfo:
            archive.extract_archive("oversize.zip", zip_bytes)
        assert excinfo.value.detail["error_code"] == "archive_entry_too_large"

    def test_malformed_archive_raises(self) -> None:
        with pytest.raises(archive.ArchiveAbort) as excinfo:
            archive.extract_archive("nope.zip", b"not a zip at all")
        assert excinfo.value.detail["error_code"] == "archive_malformed"


# --- Tar extraction --------------------------------------------------------


class TestTarExtraction:
    def test_happy_path(self) -> None:
        tar_bytes = _build_tar([("notes.md", b"# ok"), ("data.csv", b"a,b\n1,2\n")])
        result = archive.extract_archive("bundle.tar", tar_bytes)

        assert len(result.extracted) == 2
        assert result.skipped == []

    def test_symlink_skipped(self) -> None:
        # Hand-build a tar with a SYMTYPE entry next to a regular file.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tf:
            # Regular .md
            data = b"# ok"
            info = tarfile.TarInfo(name="notes.md")
            info.size = len(data)
            info.type = tarfile.REGTYPE
            tf.addfile(info, io.BytesIO(data))
            # Symlink to /etc/passwd
            link = tarfile.TarInfo(name="link.md")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tf.addfile(link)

        result = archive.extract_archive("evil.tar", buffer.getvalue())

        assert len(result.extracted) == 1
        assert result.extracted[0].filename == "notes.md"
        assert any(s.reason == "archive_unsafe_entry" for s in result.skipped)

    def test_path_traversal_skipped(self) -> None:
        tar_bytes = _build_tar([("notes.md", b"# ok"), ("../../etc/passwd.md", b"# attack")])
        result = archive.extract_archive("evil.tar", tar_bytes)

        assert len(result.extracted) == 1
        assert any(s.reason == "archive_path_traversal" for s in result.skipped)
