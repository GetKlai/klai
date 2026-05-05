"""
Markdown-aware chunker.

Splits on headings first, then falls back to paragraph + size-based splitting.
Preserves heading context in each chunk so retrieval knows what section it came from.

SPEC-RAG-PARENT-CHILD-001 (May 2026): chunk_markdown_with_parents() also
returns large parent chunks. Children embed-and-match in Qdrant; parents
replace the child text in the retrieval response so the LLM sees broader
narrative context. The legacy chunk_markdown() entry point is preserved
for callers that don't yet need parents.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    heading_path: str  # e.g. "## Section > ### Subsection"
    char_start: int
    # SPEC-RAG-PARENT-CHILD-001: index into the parent list returned by
    # chunk_markdown_with_parents. None when the chunker was called via
    # the legacy chunk_markdown entry point.
    parent_index: int | None = None


@dataclass
class ParentChunk:
    """A large parent chunk that contextualises one or more child chunks.

    Children are matched on their own (small) text; the retrieval response
    swaps in the parent's (large) text so the LLM gets broader context.
    """

    text: str
    heading_path: str
    char_start: int
    position: int  # 0-based ordering within the document
    child_indices: list[int] = field(default_factory=list)


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


# SPEC-RAG-PARENT-CHILD-001 — chunk-size defaults.
#
# Token-to-char approximations are 1 token ≈ 4 chars for mixed Dutch/English
# (mirrors enrichment._truncate_to_tokens). The SPEC targets:
#   child:   ~300 tokens / 50-token overlap   →  ~1200 chars / 200 char overlap
#   parent:  ~1500 tokens / no overlap        →  ~6000 chars / 0 overlap
#
# Parents align with the same heading-based section boundaries as the
# legacy chunker — when a section is shorter than parent_size we get one
# parent per section; when longer, parents are size-split with no overlap
# and children fan out across them.
PARENT_CHUNK_SIZE_DEFAULT = 6000
PARENT_CHUNK_OVERLAP_DEFAULT = 0
CHILD_CHUNK_SIZE_DEFAULT = 1200
CHILD_CHUNK_OVERLAP_DEFAULT = 200


def _approx_token_count(text: str) -> int:
    """Rough approximation: 1 token ≈ 4 chars."""
    return max(1, len(text) // 4)


def chunk_markdown_with_parents(
    content: str,
    child_size: int = CHILD_CHUNK_SIZE_DEFAULT,
    child_overlap: int = CHILD_CHUNK_OVERLAP_DEFAULT,
    parent_size: int = PARENT_CHUNK_SIZE_DEFAULT,
    parent_overlap: int = PARENT_CHUNK_OVERLAP_DEFAULT,
) -> tuple[list[Chunk], list[ParentChunk]]:
    """Two-tier chunking: child chunks for matching, parent chunks for context.

    Returns ``(children, parents)``. Each ``Chunk.parent_index`` points into
    the ``parents`` list. The retrieval pipeline embeds children for
    matching and swaps them for the corresponding parent text in the
    response, so the LLM gets broader narrative context.

    Section boundaries are preserved: a parent never spans two markdown
    headings (children inside a section all share that section's parent).
    """
    _, body = _strip_frontmatter(content)
    sections = _split_by_headings(body) if body.strip() else []

    if not sections:
        sections = [("", body)]

    children: list[Chunk] = []
    parents: list[ParentChunk] = []
    char_pos = 0
    parent_position = 0

    for heading_path, section_text in sections:
        # First split the section into parent-sized blocks. For typical
        # short sections (most Notion pages, FAQs) this returns a single
        # parent equal to the whole section.
        parent_texts = _split_by_size(section_text, parent_size, parent_overlap)
        for parent_text in parent_texts:
            if not parent_text.strip():
                continue
            parent_idx = len(parents)
            parent = ParentChunk(
                text=(
                    f"{heading_path}\n\n{parent_text}".strip()
                    if heading_path
                    else parent_text
                ),
                heading_path=heading_path,
                char_start=char_pos,
                position=parent_position,
            )
            parents.append(parent)
            parent_position += 1

            # Now split this parent into children for matching.
            child_texts = _split_by_size(parent_text, child_size, child_overlap)
            for child_text in child_texts:
                if not child_text.strip():
                    continue
                display = (
                    f"{heading_path}\n\n{child_text}".strip()
                    if heading_path
                    else child_text
                )
                child_idx = len(children)
                children.append(
                    Chunk(
                        text=display,
                        heading_path=heading_path,
                        char_start=char_pos,
                        parent_index=parent_idx,
                    )
                )
                parent.child_indices.append(child_idx)
                char_pos += len(child_text)

    return children, parents
