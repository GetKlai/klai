"""
HTTP client for docling-serve.
Converts files and URLs to structured text with layout metadata.
"""
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DoclingResult:
    def __init__(self, text: str, metadata: dict[str, Any]):
        self.text = text
        self.metadata = metadata


async def convert_file(file_bytes: bytes, filename: str) -> DoclingResult:
    """Send a file to docling-serve for conversion. Returns structured text."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{settings.docling_url}/v1/convert/file",
            files=[("files", (filename, file_bytes, "application/octet-stream"))],
        )
        resp.raise_for_status()
        data = resp.json()

    return _parse_response(data)


async def convert_url(url: str) -> DoclingResult:
    """Send a URL to docling-serve for conversion. Returns structured text."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.docling_url}/v1/convert/source",
            json={"sources": [{"kind": "http", "url": url}]},
        )
        resp.raise_for_status()
        data = resp.json()

    return _parse_response(data)


def _parse_response(data: dict) -> DoclingResult:
    """Extract plain text and metadata from docling-serve response."""
    # docling-serve returns document list; take first document
    documents = data.get("document", data) if "document" in data else data
    if isinstance(documents, list):
        doc = documents[0] if documents else {}
    else:
        doc = documents

    # Prefer markdown export as it preserves structure; fall back to plain text
    text = doc.get("md_content") or doc.get("text_content") or ""

    metadata = {
        "page_count": doc.get("page_count"),
        "source_name": doc.get("input", {}).get("name", ""),
    }
    return DoclingResult(text=text, metadata=metadata)
