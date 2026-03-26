"""
Markdown-aware chunker.

Splits on headings first, then falls back to paragraph + size-based splitting.
Preserves heading context in each chunk so retrieval knows what section it came from.
"""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    heading_path: str  # e.g. "## Section > ### Subsection"
    char_start: int


def _strip_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body). frontmatter_block may be empty."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[: end + 4], text[end + 4 :].lstrip("\n")
    return "", text


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Return list of (heading_path, section_text) pairs."""
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)
    last_pos = 0
    last_heading_path = ""

    for match in heading_re.finditer(text):
        if match.start() > last_pos:
            body = text[last_pos : match.start()].strip()
            if body:
                sections.append((last_heading_path, body))

        level = len(match.group(1))
        title = match.group(2).strip()
        # Trim stack to current level
        heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
        heading_stack.append((level, title))
        last_heading_path = " > ".join(t for _, t in heading_stack)
        last_pos = match.end() + 1

    if last_pos < len(text):
        body = text[last_pos:].strip()
        if body:
            sections.append((last_heading_path, body))

    return sections


def _split_by_size(text: str, size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks by character count."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            # Try to break at paragraph boundary
            break_at = text.rfind("\n\n", start, end)
            if break_at > start + size // 2:
                end = break_at
            else:
                # Fall back to sentence boundary
                break_at = text.rfind(". ", start, end)
                if break_at > start + size // 2:
                    end = break_at + 1
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]


def chunk_markdown(content: str, chunk_size: int = 1500, overlap: int = 200) -> list[Chunk]:
    """Chunk markdown content into retrieval-ready pieces."""
    _, body = _strip_frontmatter(content)
    sections = _split_by_headings(body) if body.strip() else []

    if not sections:
        # No headings — treat whole body as one section
        sections = [("", body)]

    result: list[Chunk] = []
    char_pos = 0

    for heading_path, section_text in sections:
        sub_chunks = _split_by_size(section_text, chunk_size, overlap)
        for sub in sub_chunks:
            if not sub.strip():
                continue
            # Prepend heading context to help retrieval
            display = f"{heading_path}\n\n{sub}".strip() if heading_path else sub
            result.append(Chunk(text=display, heading_path=heading_path, char_start=char_pos))
            char_pos += len(sub)

    return result
