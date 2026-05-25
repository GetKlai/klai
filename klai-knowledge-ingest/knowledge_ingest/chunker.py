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

import json
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


def normalize_document_for_chunking(content: str) -> str:
    """Return Markdown-like text suitable for chunking and embedding.

    Docs pages are stored losslessly as BlockNote JSON in Gitea so the editor
    can round-trip custom blocks. The knowledge pipeline, however, needs
    readable text. Convert a BlockNote JSON body to Markdown while preserving
    YAML frontmatter; legacy Markdown content passes through unchanged.
    """
    frontmatter, body = _strip_frontmatter(content)
    converted = _blocknote_json_to_markdown(body)
    if converted is None:
        return content
    if frontmatter:
        return f"{frontmatter}\n\n{converted}".rstrip()
    return converted


def _blocknote_json_to_markdown(body: str) -> str | None:
    trimmed = body.strip()
    if not trimmed.startswith("["):
        return None
    try:
        blocks = json.loads(trimmed)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(blocks, list)
        or not blocks
        or not all(isinstance(b, dict) and _looks_like_blocknote_block(b) for b in blocks)
    ):
        return None
    return _render_blocknote_blocks(blocks).strip()


def _looks_like_blocknote_block(block: dict) -> bool:
    block_type = block.get("type")
    content = block.get("content")
    children = block.get("children")
    if block_type is not None and not isinstance(block_type, str):
        return False
    if children is not None and not isinstance(children, list):
        return False
    if block_type in {
        "paragraph",
        "heading",
        "bulletListItem",
        "numberedListItem",
        "checkListItem",
        "codeBlock",
        "quote",
        "table",
        "image",
        "file",
    }:
        return True
    return isinstance(content, (str, list, dict)) or isinstance(children, list)


def _render_blocknote_blocks(blocks: list[dict], depth: int = 0) -> str:
    rendered: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        block_type = block.get("type")
        if block_type in {"bulletListItem", "numberedListItem", "checkListItem"}:
            items: list[str] = []
            number = 1
            while i < len(blocks) and blocks[i].get("type") == block_type:
                items.append(_render_blocknote_list_item(blocks[i], depth, number))
                number += 1
                i += 1
            rendered.append("\n".join(item for item in items if item))
            continue

        block_text = _render_blocknote_block(block, depth)
        if block_text:
            rendered.append(block_text)
        i += 1
    return "\n\n".join(rendered)


def _render_blocknote_block(block: dict, depth: int) -> str:
    block_type = block.get("type")
    content = block.get("content")
    text = "" if _is_table_content(content) else _render_inline_content(content)
    children = block.get("children")
    rendered_children = (
        _render_blocknote_blocks(children, depth + 1)
        if isinstance(children, list) and all(isinstance(c, dict) for c in children)
        else ""
    )

    if block_type == "heading":
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        level = props.get("level")
        if not isinstance(level, int):
            level = 2
        primary = f"{'#' * max(1, min(level, 6))} {text}".strip()
    elif block_type == "codeBlock":
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        language = props.get("language")
        language = language if isinstance(language, str) else ""
        primary = f"```{language}\n{_extract_inline_plain_text(content)}\n```"
    elif block_type == "quote":
        primary = "\n".join(f"> {line}" for line in text.splitlines())
    elif block_type == "table" and _is_table_content(content):
        primary = _render_blocknote_table(content)
    else:
        primary = text

    if primary and rendered_children:
        return f"{primary}\n\n{rendered_children}"
    return primary or rendered_children


def _render_blocknote_list_item(block: dict, depth: int, number: int) -> str:
    block_type = block.get("type")
    indent = "  " * depth
    text = _render_inline_content(block.get("content"))
    if block_type == "numberedListItem":
        marker = f"{number}."
    elif block_type == "checkListItem":
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        marker = "- [x]" if props.get("checked") else "- [ ]"
    else:
        marker = "-"
    line = f"{indent}{marker} {text}".rstrip()
    children = block.get("children")
    if isinstance(children, list) and all(isinstance(c, dict) for c in children):
        rendered_children = _render_blocknote_blocks(children, depth + 1)
        if rendered_children:
            return f"{line}\n{rendered_children}"
    return line


def _render_inline_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(_render_inline_item(item) for item in content)


def _render_inline_item(item: object) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""

    item_type = item.get("type")
    if item_type == "text":
        return _apply_inline_styles(str(item.get("text") or ""), item.get("styles"))
    if item_type == "link":
        label = _render_inline_content(item.get("content"))
        href = item.get("href")
        return f"[{label}]({href})" if isinstance(href, str) and href.strip() else label
    if item_type == "wikilink":
        props = item.get("props") if isinstance(item.get("props"), dict) else {}
        return str(props.get("title") or props.get("pageId") or "")
    return ""


def _apply_inline_styles(text: str, styles: object) -> str:
    if not text or not isinstance(styles, dict):
        return text
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    core = text[len(leading) : len(text) - len(trailing) if trailing else len(text)]
    if not core:
        return text
    if styles.get("code"):
        escaped_core = core.replace("`", "\\`")
        core = f"`{escaped_core}`"
    if styles.get("bold"):
        core = f"**{core}**"
    if styles.get("italic"):
        core = f"_{core}_"
    if styles.get("strike"):
        core = f"~~{core}~~"
    return f"{leading}{core}{trailing}"


def _extract_inline_plain_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "link":
                parts.append(_extract_inline_plain_text(item.get("content")))
            elif item.get("type") == "wikilink":
                props = item.get("props") if isinstance(item.get("props"), dict) else {}
                parts.append(str(props.get("title") or ""))
    return "".join(parts)


def _is_table_content(content: object) -> bool:
    return (
        isinstance(content, dict)
        and content.get("type") == "tableContent"
        and isinstance(content.get("rows"), list)
    )


def _table_cell_content(cell: object) -> object:
    if isinstance(cell, list):
        return cell
    if isinstance(cell, dict):
        return cell.get("content")
    return None


def _render_blocknote_table(content: dict) -> str:
    rows = content.get("rows")
    if not isinstance(rows, list) or not rows:
        return ""
    parsed_rows: list[list[str]] = []
    width = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
            continue
        cells = [
            _render_inline_content(_table_cell_content(cell)).replace("\n", " ").strip()
            for cell in row["cells"]
        ]
        parsed_rows.append(cells)
        width = max(width, len(cells))
    if not parsed_rows or width == 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in parsed_rows]
    lines = [f"| {' | '.join(cell or ' ' for cell in normalized[0])} |"]
    lines.append(f"|{' --- |' * width}")
    lines.extend(f"| {' | '.join(cell or ' ' for cell in row)} |" for row in normalized[1:])
    return "\n".join(lines)


def _find_code_block_ranges(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` char ranges of fenced code blocks.

    Recognises CommonMark fenced code blocks: an opening line of 3+
    backticks (``` ``` ```) or 3+ tildes (``~~~``), terminated by a
    line of the same fence character with at least as many markers.
    Unterminated fences extend to end-of-text.

    Audit 2026-05-06 finding 8 — without this, ``# something`` lines
    inside a Python / shell code example get parsed as headings and
    leak into the chunked document's ``heading_path``.
    """
    fence_re = re.compile(r"^([`~]{3,})", re.MULTILINE)
    matches = list(fence_re.finditer(text))

    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(matches):
        opener = matches[i]
        opener_chars = opener.group(1)
        opener_char = opener_chars[0]
        opener_len = len(opener_chars)

        # Look for the next fence that closes this one: same character,
        # length >= opener length. Anything in between (including other
        # fence-like lines that don't match) is body of the block.
        j = i + 1
        closed = False
        while j < len(matches):
            closer = matches[j]
            closer_chars = closer.group(1)
            if closer_chars[0] == opener_char and len(closer_chars) >= opener_len:
                ranges.append((opener.start(), closer.end()))
                i = j + 1
                closed = True
                break
            j += 1
        if not closed:
            # Unterminated fence — block runs to end of text.
            ranges.append((opener.start(), len(text)))
            break

    return ranges


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Return list of (heading_path, section_text) pairs.

    Skips heading-shaped lines (`# ...`) that fall inside fenced code
    blocks — see ``_find_code_block_ranges``.
    """
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)
    last_pos = 0
    last_heading_path = ""

    code_ranges = _find_code_block_ranges(text)

    def _inside_code_block(pos: int) -> bool:
        for start, end in code_ranges:
            if start <= pos < end:
                return True
        return False

    for match in heading_re.finditer(text):
        # Audit finding 8: skip code-comment lines inside fenced blocks.
        if _inside_code_block(match.start()):
            continue

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
                text=(f"{heading_path}\n\n{parent_text}".strip() if heading_path else parent_text),
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
                display = f"{heading_path}\n\n{child_text}".strip() if heading_path else child_text
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
