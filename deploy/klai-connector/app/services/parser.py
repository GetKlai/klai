"""Unstructured.io document parser wrapper with temp file cleanup."""

import tempfile
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def parse_document(content: bytes, filename: str) -> str:
    """Parse a document using Unstructured.io and return extracted text.

    The content is written to a temporary file which is cleaned up after parsing.

    Args:
        content: Raw file bytes.
        filename: Original filename (used for format detection).

    Returns:
        Extracted text content as a single string with double-newline separators.

    Raises:
        ValueError: If the file exceeds the 50 MB size limit.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE} bytes)")

    from unstructured.partition.auto import partition

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / filename
        filepath.write_bytes(content)
        elements = partition(str(filepath))
        text = "\n\n".join(str(e) for e in elements if str(e).strip())

    logger.info("Parsed document %s: %d characters extracted", filename, len(text))
    return text
