"""Document parser: plain text for text formats, Unstructured for binary formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Text-based formats that can be decoded directly without Unstructured
_PLAIN_TEXT_SUFFIXES = {".md", ".txt", ".rst", ".csv"}


def partition(filepath: str) -> list:  # type: ignore[type-arg]
    """Lazy-import wrapper for ``unstructured.partition.auto.partition``."""
    from unstructured.partition.auto import partition as _partition

    return _partition(filepath)  # type: ignore[no-any-return]


@dataclass
class ParseResult:
    """Result of document parsing, containing text and optional images."""

    text: str
    images: list[dict[str, str]] = field(default_factory=list)


def parse_document(content: bytes, filename: str) -> str:
    """Parse a document and return extracted text.

    Text-based formats (.md, .txt, .rst, .csv) and files with no extension
    are decoded directly as UTF-8.
    Binary formats (.pdf, .docx, .html) use Unstructured.io.

    Args:
        content: Raw file bytes.
        filename: Original filename (used for format detection).

    Returns:
        Extracted text content as a string.

    Raises:
        ValueError: If the file exceeds the 50 MB size limit.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE} bytes)")

    suffix = Path(filename).suffix.lower()

    # Files with no extension, invalid extensions (containing spaces or longer than 10 chars,
    # e.g. Notion page titles like "3.011 Opzeggingen..."), or known text formats are decoded
    # directly as UTF-8 without going to Unstructured.
    if not suffix or " " in suffix or len(suffix) > 10 or suffix in _PLAIN_TEXT_SUFFIXES:
        text = content.decode("utf-8", errors="replace")
        logger.info("Parsed text document %s: %d characters", filename, len(text))
        return text

    elements = partition(str(_write_temp(content, filename)))
    text = "\n\n".join(str(e) for e in elements if str(e).strip())

    logger.info("Parsed document %s: %d characters extracted", filename, len(text))
    return text


def parse_document_with_images(content: bytes, filename: str) -> ParseResult:
    """Parse a document and return extracted text plus embedded images.

    Text-based formats return text only (no embedded images).
    Binary formats (PDF, DOCX) may yield ``Image`` elements from Unstructured
    that contain base64-encoded image data in their metadata.

    Args:
        content: Raw file bytes.
        filename: Original filename (used for format detection).

    Returns:
        :class:`ParseResult` with extracted text and a list of image dicts.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE} bytes)")

    suffix = Path(filename).suffix.lower()

    if not suffix or " " in suffix or len(suffix) > 10 or suffix in _PLAIN_TEXT_SUFFIXES:
        text = content.decode("utf-8", errors="replace")
        logger.info("Parsed text document %s: %d characters", filename, len(text))
        return ParseResult(text=text)

    elements = partition(str(_write_temp(content, filename)))

    text_parts: list[str] = []
    images: list[dict[str, str]] = []

    for elem in elements:
        text_str = str(elem).strip()
        if text_str:
            text_parts.append(text_str)

        if getattr(elem, "category", None) == "Image":
            b64 = getattr(elem.metadata, "image_base64", None)
            mime = getattr(elem.metadata, "image_mime_type", None)
            if b64:
                images.append({"data_b64": b64, "mime_type": mime or "image/png"})

    text = "\n\n".join(text_parts)
    logger.info(
        "Parsed document %s: %d characters, %d images extracted",
        filename, len(text), len(images),
    )
    return ParseResult(text=text, images=images)


def _write_temp(content: bytes, filename: str) -> Path:
    """Write content to a temporary file and return the path.

    The caller is responsible for cleanup (or relies on OS temp cleanup).
    """
    import tempfile as _tf

    tmpdir = _tf.mkdtemp()
    filepath = Path(tmpdir) / filename
    filepath.write_bytes(content)
    return filepath
