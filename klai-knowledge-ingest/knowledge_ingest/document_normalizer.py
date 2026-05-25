"""Normalize source document bodies before chunking and embedding."""

from __future__ import annotations

from knowledge_ingest.blocknote_markdown import blocknote_json_to_markdown


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_block, body)``; frontmatter may be empty."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[: end + 4], text[end + 4 :].lstrip("\n")
    return "", text


def normalize_document_for_chunking(content: str) -> str:
    """Return Markdown-like text suitable for chunking and embedding.

    Legacy Markdown passes through unchanged. Docs pages stored as BlockNote
    JSON are rendered to Markdown-like text while preserving YAML frontmatter.
    """
    frontmatter, body = split_frontmatter(content)
    converted = blocknote_json_to_markdown(body)
    if converted is None:
        return content
    if frontmatter:
        return f"{frontmatter}\n\n{converted}".rstrip()
    return converted
