"""Document parser: plain text for text formats, Unstructured for binary formats."""

import tempfile
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Text-based formats that can be decoded directly without Unstructured
_PLAIN_TEXT_SUFFIXES = {".md", ".txt", ".rst", ".csv"}


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

    # Files with no extension (e.g. Notion pages returned as plain text) are decoded directly.
    if not suffix or suffix in _PLAIN_TEXT_SUFFIXES:
        text = content.decode("utf-8", errors="replace")
        logger.info("Parsed text document %s: %d characters", filename, len(text))
        return text

    from unstructured.partition.auto import partition

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / filename
        filepath.write_bytes(content)
        elements = partition(str(filepath))
        text = "\n\n".join(str(e) for e in elements if str(e).strip())

    logger.info("Parsed document %s: %d characters extracted", filename, len(text))
    return text
