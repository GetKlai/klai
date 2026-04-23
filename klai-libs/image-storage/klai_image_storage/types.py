"""Typed value objects exposed by klai-image-storage.

Keeps the lib's public surface explicit instead of the stringly-typed
``dict[str, str]`` it used to accept for parser-extracted images.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedImage:
    """An already-decoded image emitted by a document parser.

    Callers that extract images from PDF/DOCX/other parsed payloads
    decode the bytes themselves (e.g. via :func:`base64.b64decode`) and
    pass them in via this dataclass. This keeps the shared lib free
    from parser-specific envelope knowledge (MIME tables, base64 keys,
    Unstructured field names, ...).

    Args:
        data: Raw image bytes.
        ext: File extension without the leading dot (``"png"``,
            ``"jpg"``, ``"svg"``, ...). The caller translates their
            own MIME/type identifier into a normal extension.
        source_id: Optional identifier for log correlation (document
            path, Notion block ID, Unstructured element ID, ...). Used
            verbatim in structured log fields when an image fails.
    """

    data: bytes
    ext: str
    source_id: str = ""
