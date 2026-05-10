"""Unit tests for file_upload service (SPEC-KB-FILE-UPLOAD-001 Phase 1A).

Pure-function tests on the validation helpers. No DB, no httpx, no
FastAPI test client — the wider integration is covered by
``test_app_knowledge_sources_file.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.file_upload import (
    FULL_WHITELIST,
    MAX_TEXT_FILE_BYTES,
    PHASE_1_TEXT_EXTENSIONS,
    assert_size_within_text_cap,
    build_source_ref,
    classify_extension,
    derive_title,
    get_extension,
    normalise_text_content,
    validate_text_upload,
)


class TestGetExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("foo.md", ".md"),
            ("Foo.PDF", ".pdf"),
            ("foo.tar.gz", ".gz"),
            ("noext", ""),
            ("UPPER.MD", ".md"),
            ("path/with/slash.txt", ".txt"),
        ],
    )
    def test_extracts_lowercase_suffix(self, filename: str, expected: str) -> None:
        assert get_extension(filename) == expected


class TestClassifyExtension:
    @pytest.mark.parametrize("ext", sorted(PHASE_1_TEXT_EXTENSIONS))
    def test_phase_1_text_extensions_route_to_phase1(self, ext: str) -> None:
        result_ext, phase = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert phase == "phase1"

    @pytest.mark.parametrize(
        "ext",
        sorted(FULL_WHITELIST - PHASE_1_TEXT_EXTENSIONS),
    )
    def test_full_whitelist_non_phase_1_returns_phase_pending(self, ext: str) -> None:
        result_ext, phase = classify_extension(f"foo{ext}")
        assert result_ext == ext
        assert phase == "phase_pending"

    @pytest.mark.parametrize(
        "filename",
        ["foo.exe", "script.sh", "binary.bin", "page.html"],
    )
    def test_unknown_extension_raises_400(self, filename: str) -> None:
        with pytest.raises(HTTPException) as excinfo:
            classify_extension(filename)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    def test_no_extension_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            classify_extension("readme")
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"


class TestAssertSizeWithinTextCap:
    def test_ok_at_cap(self) -> None:
        assert_size_within_text_cap(MAX_TEXT_FILE_BYTES)

    def test_ok_below_cap(self) -> None:
        assert_size_within_text_cap(1)

    def test_zero_ok(self) -> None:
        # Empty content is rejected later (empty_content); size guard alone
        # accepts zero so the more specific reason can surface.
        assert_size_within_text_cap(0)

    def test_one_over_cap_raises_413(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            assert_size_within_text_cap(MAX_TEXT_FILE_BYTES + 1)
        assert excinfo.value.status_code == 413
        assert excinfo.value.detail["error_code"] == "file_too_large"


class TestNormaliseTextContent:
    def test_plain_utf8(self) -> None:
        assert normalise_text_content(b"hello") == "hello"

    def test_strips_utf8_bom(self) -> None:
        assert normalise_text_content(b"\xef\xbb\xbfhello") == "hello"

    def test_utf8_multibyte(self) -> None:
        assert normalise_text_content("café".encode()) == "café"

    def test_cp1252_fallback(self) -> None:
        # 0x96 is en-dash in cp1252, invalid as UTF-8.
        result = normalise_text_content(b"hello\x96world")
        assert "hello" in result
        assert "world" in result

    def test_invalid_encoding_raises_400(self) -> None:
        # Construct bytes that fail BOTH utf-8 and cp1252 decode.
        # cp1252 actually decodes most byte sequences (it's a single-byte
        # encoding with only 5 undefined slots: 0x81 0x8D 0x8F 0x90 0x9D).
        bad = bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])
        with pytest.raises(HTTPException) as excinfo:
            normalise_text_content(bad)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "invalid_text_encoding"


class TestBuildSourceRef:
    def test_prefix(self) -> None:
        assert build_source_ref("hello").startswith("file:sha256:")

    def test_deterministic(self) -> None:
        assert build_source_ref("hello") == build_source_ref("hello")

    def test_different_content_different_ref(self) -> None:
        assert build_source_ref("a") != build_source_ref("b")


class TestDeriveTitle:
    @pytest.mark.parametrize(
        ("filename", "ext", "expected"),
        [
            ("notes.md", ".md", "notes"),
            ("My Doc.txt", ".txt", "My Doc"),
            ("data.csv", ".csv", "data"),
            ("  spaced  .md", ".md", "spaced"),
            ("noext", "", "noext"),
            (".md", ".md", "untitled"),
            ("", "", "untitled"),
        ],
    )
    def test_strips_extension_and_whitespace(self, filename: str, ext: str, expected: str) -> None:
        assert derive_title(filename, ext) == expected


class TestValidateTextUpload:
    def test_happy_path_md(self) -> None:
        result = validate_text_upload("notes.md", b"# Hello\n\ncontent")
        assert result.filename == "notes.md"
        assert result.extension == ".md"
        assert result.content == "# Hello\n\ncontent"
        assert result.title == "notes"
        assert result.bytes_count == len(b"# Hello\n\ncontent")
        assert result.source_ref.startswith("file:sha256:")

    def test_happy_path_csv_with_bom(self) -> None:
        result = validate_text_upload("rows.csv", b"\xef\xbb\xbfa,b,c\n1,2,3\n")
        assert result.content == "a,b,c\n1,2,3\n"
        assert result.title == "rows"

    def test_phase_pending_raises_400_phase_pending(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("doc.pdf", b"%PDF-1.4")
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "phase_pending"
        assert excinfo.value.detail["extension"] == ".pdf"

    def test_unsupported_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("script.sh", b"#!/bin/bash")
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "unsupported_extension"

    def test_empty_content_raises_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("empty.md", b"   \n  ")
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["error_code"] == "empty_content"

    def test_oversize_raises_413(self) -> None:
        oversize = b"a" * (MAX_TEXT_FILE_BYTES + 1)
        with pytest.raises(HTTPException) as excinfo:
            validate_text_upload("big.txt", oversize)
        assert excinfo.value.status_code == 413
        assert excinfo.value.detail["error_code"] == "file_too_large"
