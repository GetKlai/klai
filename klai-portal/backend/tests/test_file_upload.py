"""Unit tests for file_upload service (SPEC-KB-FILE-UPLOAD-001).

Pure-function tests on the validation helpers. Wider integration is
covered by ``test_app_knowledge_sources_file.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.file_upload import (
    ARCHIVE_EXTENSIONS,
    DOCLING_EXTENSIONS,
    FULL_WHITELIST,
    MAX_BINARY_FILE_BYTES,
    MAX_TEXT_FILE_BYTES,
    PENDING_EXTENSIONS,
    TEXT_EXTENSIONS,
    assert_size_within_binary_cap,
    assert_size_within_text_cap,
    build_source_ref,
    classify_extension,
    derive_title,
    detect_mime,
    docling_format_for,
    get_extension,
    normalise_text_content,
    sanitize_filename,
    validate_binary_upload,
    validate_text_upload,
)


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("notes.md", "notes.md"),
            ("  notes.md  ", "notes.md"),
            ("", "untitled"),
            (None, "untitled"),
            ("   ", "untitled"),
            ("../../etc/passwd.md", "passwd.md"),
            ("/etc/passwd.md", "passwd.md"),
            ("C:\\Users\\bob\\notes.md", "notes.md"),
            ("hello\x00world.md", "helloworld.md"),
            ("nl\nin\tname.md", "nlinname.md"),
            ('weird<>:"|?*.md', "weird.md"),
        ],
    )
    def test_normalises_known_attacks(self, raw: str | None, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_truncates_overlong_name_preserving_extension(self) -> None:
        long_stem = "a" * 300
        result = sanitize_filename(f"{long_stem}.pdf")
        assert len(result) == 255
        assert result.endswith(".pdf")
        assert result.startswith("a" * (255 - 4))


class TestGetExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("foo.md", ".md"),
            ("Foo.PDF", ".pdf"),
            ("foo.tar.gz", ".gz"),
            ("noext", ""),
        ],
    )
    def test_extracts_lowercase_suffix(self, filename: str, expected: str) -> None:
        assert get_extension(filename) == expected


class TestClassifyExtension:
    @pytest.mark.parametrize("ext", sorted(TEXT_EXTENSIONS))
    def test_text_extensions_route_to_text_pipeline(self, ext: str) -> None:
        result_ext, pipeline = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert pipeline == "text"

    @pytest.mark.parametrize("ext", sorted(DOCLING_EXTENSIONS))
    def test_docling_extensions_route_to_docling_pipeline(self, ext: str) -> None:
        result_ext, pipeline = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert pipeline == "docling"

    @pytest.mark.parametrize("ext", sorted(ARCHIVE_EXTENSIONS))
    def test_archive_extensions_route_to_archive_pipeline(self, ext: str) -> None:
        result_ext, pipeline = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert pipeline == "archive"

    @pytest.mark.parametrize("ext", sorted(PENDING_EXTENSIONS))
    def test_pending_extensions_route_to_phase_pending(self, ext: str) -> None:
        result_ext, pipeline = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert pipeline == "phase_pending"

    def test_full_whitelist_partition(self) -> None:
        # Every entry in FULL_WHITELIST routes to exactly one pipeline.
        assert TEXT_EXTENSIONS | DOCLING_EXTENSIONS | ARCHIVE_EXTENSIONS | PENDING_EXTENSIONS == FULL_WHITELIST
        for a, b in (
            (TEXT_EXTENSIONS, DOCLING_EXTENSIONS),
            (TEXT_EXTENSIONS, ARCHIVE_EXTENSIONS),
            (TEXT_EXTENSIONS, PENDING_EXTENSIONS),
            (DOCLING_EXTENSIONS, ARCHIVE_EXTENSIONS),
            (DOCLING_EXTENSIONS, PENDING_EXTENSIONS),
            (ARCHIVE_EXTENSIONS, PENDING_EXTENSIONS),
        ):
            assert a.isdisjoint(b)

    @pytest.mark.parametrize("filename", ["foo.exe", "script.sh", "page.html"])
    def test_unknown_extension_raises_400(self, filename: str) -> None:
        with pytest.raises(HTTPException) as excinfo:
            classify_extension(filename)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    def test_no_extension_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            classify_extension("readme")
        assert excinfo.value.status_code == 400


class TestDoclingFormatFor:
    @pytest.mark.parametrize(
        ("ext", "expected"),
        [
            (".pdf", "pdf"),
            (".docx", "docx"),
            (".xlsx", "xlsx"),
            (".pptx", "pptx"),
            (".json", "json"),
            (".xml", "xml"),
        ],
    )
    def test_known_extensions(self, ext: str, expected: str) -> None:
        assert docling_format_for(ext) == expected


class TestSizeCaps:
    def test_text_cap_ok_at_boundary(self) -> None:
        assert_size_within_text_cap(MAX_TEXT_FILE_BYTES)

    def test_text_cap_one_over(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            assert_size_within_text_cap(MAX_TEXT_FILE_BYTES + 1)
        assert excinfo.value.status_code == 413
        assert excinfo.value.detail["error_code"] == "file_too_large"

    def test_binary_cap_ok_at_boundary(self) -> None:
        assert_size_within_binary_cap(MAX_BINARY_FILE_BYTES)

    def test_binary_cap_one_over(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            assert_size_within_binary_cap(MAX_BINARY_FILE_BYTES + 1)
        assert excinfo.value.status_code == 413


class TestNormaliseTextContent:
    def test_plain_utf8(self) -> None:
        assert normalise_text_content(b"hello") == "hello"

    def test_strips_utf8_bom(self) -> None:
        assert normalise_text_content(b"\xef\xbb\xbfhello") == "hello"

    def test_cp1252_fallback(self) -> None:
        result = normalise_text_content(b"hello\x96world")
        assert "hello" in result and "world" in result

    def test_invalid_encoding_raises_400(self) -> None:
        # 5 bytes that fail both utf-8 and cp1252.
        bad = bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])
        with pytest.raises(HTTPException) as excinfo:
            normalise_text_content(bad)
        assert excinfo.value.detail["error_code"] == "invalid_text_encoding"


class TestBuildSourceRef:
    def test_text_and_bytes_share_hash_space(self) -> None:
        # ``build_source_ref`` accepts both — same content yields same hash.
        text_ref = build_source_ref("hello")
        bytes_ref = build_source_ref(b"hello")
        assert text_ref == bytes_ref
        assert text_ref.startswith("file:sha256:")

    def test_different_bytes_different_ref(self) -> None:
        assert build_source_ref(b"a") != build_source_ref(b"b")


class TestDeriveTitle:
    @pytest.mark.parametrize(
        ("filename", "ext", "expected"),
        [
            ("notes.md", ".md", "notes"),
            ("Chemie 4VWO.pdf", ".pdf", "Chemie 4VWO"),
            ("data.csv", ".csv", "data"),
            (".md", ".md", "untitled"),
            ("", "", "untitled"),
        ],
    )
    def test_strips_extension_and_whitespace(self, filename: str, ext: str, expected: str) -> None:
        assert derive_title(filename, ext) == expected


class TestValidateTextUpload:
    def test_happy_path(self) -> None:
        result = validate_text_upload("notes.md", b"# Hello\nbody")
        assert result.filename == "notes.md"
        assert result.extension == ".md"
        assert result.content == "# Hello\nbody"
        assert result.title == "notes"
        assert result.source_ref.startswith("file:sha256:")

    def test_csv_with_bom(self) -> None:
        result = validate_text_upload("rows.csv", b"\xef\xbb\xbfa,b\n1,2\n")
        assert result.content == "a,b\n1,2\n"

    def test_pdf_raises_wrong_pipeline(self) -> None:
        # PDF is now docling-pipeline, not text. Must route through
        # validate_binary_upload, not validate_text_upload.
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("doc.pdf", b"%PDF-1.4")
        assert excinfo.value.detail["error_code"] == "wrong_pipeline_for_text"

    def test_unsupported_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("script.sh", b"#!/bin/bash")
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    def test_empty_content_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("empty.md", b"   \n  ")
        assert excinfo.value.detail["error_code"] == "empty_content"


# A minimal valid PDF (28 bytes — tells filetype.guess that this is PDF).
_TINY_PDF = b"%PDF-1.4\n%%EOF\n"

# A minimal DOCX/XLSX/PPTX is a zip — filetype detects ``application/zip``
# which differs from the OOX expected mime. We exercise the ``.json`` /
# ``.xml`` path which skips magic-byte detection in unit tests, and rely
# on integration tests for OOX.


class TestDetectMime:
    def test_pdf_magic_byte_match(self) -> None:
        assert detect_mime(".pdf", _TINY_PDF) == "application/pdf"

    def test_pdf_magic_byte_mismatch(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            detect_mime(".pdf", b"GIF89a\x00\x00")
        assert excinfo.value.detail["error_code"] == "mime_mismatch"

    def test_pdf_empty_body_raises_empty_content(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            detect_mime(".pdf", b"")
        assert excinfo.value.detail["error_code"] == "empty_content"

    def test_json_skips_magic_byte_check(self) -> None:
        # JSON has no binary magic bytes; we trust the extension.
        assert detect_mime(".json", b'{"k": "v"}') == "text/plain"

    def test_xml_skips_magic_byte_check(self) -> None:
        assert detect_mime(".xml", b"<root/>") == "text/plain"

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            detect_mime(".exe", b"MZ\x90\x00")
        assert excinfo.value.detail["error_code"] == "unsupported_extension"


class TestValidateBinaryUpload:
    def test_happy_pdf(self) -> None:
        result = validate_binary_upload("chemie.pdf", _TINY_PDF)
        assert result.extension == ".pdf"
        assert result.docling_format == "pdf"
        assert result.mime == "application/pdf"
        assert result.content == _TINY_PDF
        assert result.title == "chemie"
        assert result.source_ref.startswith("file:sha256:")

    def test_happy_json(self) -> None:
        result = validate_binary_upload("data.json", b'{"k": "v"}')
        assert result.docling_format == "json"
        assert result.title == "data"

    def test_text_extension_raises_wrong_pipeline(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_binary_upload("notes.md", b"hello")
        assert excinfo.value.detail["error_code"] == "wrong_pipeline_for_binary"

    def test_pending_extension_raises_wrong_pipeline(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_binary_upload("archive.zip", b"PK\x03\x04")
        assert excinfo.value.detail["error_code"] == "wrong_pipeline_for_binary"

    def test_oversize_raises_413(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_binary_upload("big.pdf", b"%PDF-1.4\n" + b"x" * MAX_BINARY_FILE_BYTES)
        assert excinfo.value.status_code == 413
        assert excinfo.value.detail["error_code"] == "file_too_large"

    def test_mime_mismatch_raises_400(self) -> None:
        # filetype detects this as image/gif, not application/pdf.
        with pytest.raises(HTTPException) as excinfo:
            validate_binary_upload("fake.pdf", b"GIF89a\x00\x00\x00\x00")
        assert excinfo.value.detail["error_code"] == "mime_mismatch"
