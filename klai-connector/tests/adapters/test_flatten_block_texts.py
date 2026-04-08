"""Tests for _flatten_block_texts filtering logic.

Verifies that child_page/child_database reference strings and expiring
S3 presigned URLs from media blocks are excluded from extracted text.
"""

from __future__ import annotations

from app.adapters.notion import _flatten_block_texts


def _paragraph(text: str, children: list | None = None) -> dict:
    block = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"plain_text": text}],
        },
    }
    if children:
        block["_children"] = children
    return block


def _child_page(title: str) -> dict:
    return {
        "type": "child_page",
        "child_page": {"title": title},
        "has_children": False,
    }


def _child_database(title: str) -> dict:
    return {
        "type": "child_database",
        "child_database": {"title": title},
        "has_children": False,
    }


def _image_block(url: str, caption: str = "") -> dict:
    block: dict = {
        "type": "image",
        "image": {
            "type": "file",
            "file": {"url": url},
            "caption": [],
        },
    }
    if caption:
        block["image"]["caption"] = [{"plain_text": caption}]
    return block


def _file_block(url: str) -> dict:
    return {
        "type": "file",
        "file": {
            "type": "file",
            "file": {"url": url},
            "caption": [],
        },
    }


def _video_block(url: str) -> dict:
    return {
        "type": "video",
        "video": {
            "type": "file",
            "file": {"url": url},
            "caption": [],
        },
    }


# ---------------------------------------------------------------------------
# Paragraph text extraction (baseline — should still work)
# ---------------------------------------------------------------------------


def test_paragraph_text_extracted() -> None:
    blocks = [_paragraph("Hello world")]
    assert _flatten_block_texts(blocks) == ["Hello world"]


def test_nested_paragraph_extracted() -> None:
    blocks = [_paragraph("Parent", children=[_paragraph("Child")])]
    result = _flatten_block_texts(blocks)
    assert "Parent" in result
    assert "Child" in result


# ---------------------------------------------------------------------------
# child_page / child_database blocks are skipped
# ---------------------------------------------------------------------------


def test_child_page_skipped() -> None:
    blocks = [
        _paragraph("Before"),
        _child_page("Support wiki Voys en Nerds!"),
        _paragraph("After"),
    ]
    result = _flatten_block_texts(blocks)
    assert result == ["Before", "After"]
    assert not any("child_page" in t for t in result)
    assert not any("Support wiki" in t for t in result)


def test_child_database_skipped() -> None:
    blocks = [
        _child_database("My Database"),
        _paragraph("Content"),
    ]
    result = _flatten_block_texts(blocks)
    assert result == ["Content"]


# ---------------------------------------------------------------------------
# Media blocks: presigned URLs filtered, captions kept
# ---------------------------------------------------------------------------


def test_image_presigned_url_filtered() -> None:
    """S3 presigned URLs from image blocks should not appear in text."""
    url = (
        "https://prod-files-secure.s3.us-west-2.amazonaws.com/abc123/"
        "image.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential="
        "AKIA%2F20260408%2Fus-west-2%2Fs3%2Faws4_request"
        "&X-Amz-Signature=bf23b0dc563c6ab5f3b1b235357c3807"
    )
    blocks = [_image_block(url)]
    result = _flatten_block_texts(blocks)
    assert result == []
    # Verify no S3 URL fragments leak through
    assert not any("amazonaws" in t for t in result)
    assert not any("X-Amz" in t for t in result)


def test_image_caption_kept() -> None:
    """Image captions are useful context and should be preserved."""
    url = "https://prod-files-secure.s3.amazonaws.com/long-presigned-url"
    blocks = [_image_block(url, caption="Screenshot of the dashboard")]
    result = _flatten_block_texts(blocks)
    assert result == ["Screenshot of the dashboard"]


def test_file_block_url_filtered() -> None:
    url = "https://prod-files-secure.s3.amazonaws.com/some-file.pdf?X-Amz-Signature=abc"
    blocks = [_file_block(url)]
    assert _flatten_block_texts(blocks) == []


def test_video_block_url_filtered() -> None:
    url = "https://prod-files-secure.s3.amazonaws.com/video.mp4?X-Amz-Signature=abc"
    blocks = [_video_block(url)]
    assert _flatten_block_texts(blocks) == []


# ---------------------------------------------------------------------------
# Mixed content — realistic scenario
# ---------------------------------------------------------------------------


def test_mixed_content_realistic() -> None:
    """Realistic wiki page with text, images, child pages, and nested blocks."""
    blocks = [
        _paragraph("Welcome to the support wiki"),
        _child_page("Voys: Our tone of voice"),
        _image_block(
            "https://prod-files-secure.s3.amazonaws.com/presigned-garbage",
            caption="Company logo",
        ),
        _paragraph("Important information", children=[
            _paragraph("Nested detail"),
            _image_block("https://prod-files-secure.s3.amazonaws.com/another-url"),
        ]),
        _child_page("Callerlist & statistics"),
        _paragraph("End of page"),
    ]
    result = _flatten_block_texts(blocks)
    assert result == [
        "Welcome to the support wiki",
        "Company logo",
        "Important information",
        "Nested detail",
        "End of page",
    ]


def test_empty_blocks() -> None:
    assert _flatten_block_texts([]) == []
