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

from knowledge_ingest.document_normalizer import split_frontmatter
from knowledge_ingest.utils.link_density import link_density


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


def _fits_whole(text: str, start: int, size: int) -> bool:
    """True when the paragraph starting at ``start`` is at most ``size`` long."""
    end = text.find("\n\n", start)
    return (len(text) if end == -1 else end) - start <= size


def _split_by_size(text: str, size: int, overlap: int, keep_paragraphs: bool = False) -> list[str]:
    """Split text into overlapping chunks by character count.

    ``keep_paragraphs`` (children only) also breaks at a paragraph boundary before
    the halfway point when the paragraph after it fits a chunk whole, so it moves
    to the next chunk instead of being cut: the JSON-feed adapter relies on it for
    record lines of up to 900 chars in a 1000-char chunk. The early break is taken
    only when it leaves a chunk of at least ``MIN_SECTION_TOKENS`` (no stub, the
    same floor ``_merge_stubs`` applies to sections) and moves the window forward
    by more than ``overlap``, otherwise ``start = end - overlap`` would not advance.
    """
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            # Try to break at paragraph boundary
            break_at = text.rfind("\n\n", start, end)
            early = (
                keep_paragraphs
                and break_at - start >= max(MIN_SECTION_TOKENS * 4, overlap + 1)
                and _fits_whole(text, break_at + 2, size)
            )
            if break_at > start + size // 2 or early:
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
    _, body = split_frontmatter(content)
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
# (mirrors enrichment._truncate_to_tokens).
#   child:   ~250 tokens / no overlap   →  ~1000 chars / 0 overlap
#   parent:  ~1500 tokens / no overlap  →  ~6000 chars / 0 overlap
#
# Children used to be 300-800 tokens with 200 chars of overlap. Chroma's chunking
# evaluation (plan §3) has 200-token recursive chunks without overlap beating
# 800/400 on precision at equal recall, and the selection step verifies quotes
# per passage, so it does not need whole sections. Overlap is 0 because the
# retrieval side builds its window from the neighbouring children, so overlap
# would only repeat text the window already adds.
#
# Parents align with the same heading-based section boundaries as the
# legacy chunker — when a section is shorter than parent_size we get one
# parent per section; when longer, parents are size-split with no overlap
# and children fan out across them.
PARENT_CHUNK_SIZE_DEFAULT = 6000
PARENT_CHUNK_OVERLAP_DEFAULT = 0
CHILD_CHUNK_SIZE_DEFAULT = 1000
CHILD_CHUNK_OVERLAP_DEFAULT = 0


def _approx_token_count(text: str) -> int:
    """Rough approximation: 1 token ≈ 4 chars."""
    return max(1, len(text) // 4)


# A section whose link labels are at least this share of its visible text is a
# list of links (related articles, menus), not something a question can be
# answered from. link_density() on whole pages puts articles at 5-10% and
# navigation-only pages at 80%+; on sections, a synthetic bare list of three
# links measures 0.86, the same list under a one-line "Zie ook:" 0.74, a list
# whose items each carry a short description 0.30, and a sentence with one link
# 0.17. 0.5 sits in the gap between the described list (kept) and the bare
# one (dropped). A dropped section is dropped from chunking only: its links reach
# the link graph from the crawl's own link list (page_links), not from the
# chunks, so links_to and the anchor texts are unaffected.
LINK_BLOCK_DENSITY = 0.5

# A section with fewer body tokens (heading excluded, _approx_token_count) is a
# stub: one or two sentences, which embeds as a vague point of its own. On
# production 20% of the 86k parent chunks hold fewer than 60 tokens, heading
# included. Docling's HybridChunker merges such peers the same way.
MIN_SECTION_TOKENS = 50

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

Section = tuple[str, str]  # (heading_path, body)


def _parent_path(path: str) -> str:
    return path.rpartition(" > ")[0]


def _without_code(text: str) -> str:
    """Text with fenced code blocks cut out: Markdown shown as an example is not a link."""
    parts: list[str] = []
    pos = 0
    for start, end in _find_code_block_ranges(text):
        parts.append(text[pos:start])
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


def _drop_link_blocks(sections: list[Section]) -> tuple[list[Section], int]:
    """Drop link-list sections, unless nothing else would be left of the document.

    An index page that is only links keeps them: it is all there is to retrieve,
    and an empty chunk list would turn the artifact into "empty document".
    """
    kept = [
        s
        for s in sections
        if link_density(_IMAGE_RE.sub("", _without_code(s[1]))) < LINK_BLOCK_DENSITY
    ]
    if not kept:
        return sections, 0
    return kept, len(sections) - len(kept)


def _merge_stubs(sections: list[Section]) -> tuple[list[Section], int]:
    """Merge each stub into a neighbour of the same parent heading.

    A stub goes into the following section when that is a sibling of the stub or
    sits under it. When there is no such section (the stub is the last one under
    its parent heading, or the last of the document), it goes into the preceding
    sibling instead. A stub with no eligible neighbour, including the only
    section of a document, stays as it is. Several stubs in a row end up in the
    first section that is long enough.
    """

    def eligible(stub_path: str, other_path: str) -> bool:
        # Two top-level headings have no parent heading in common, so they are
        # not peers; a stub before the first heading goes with that heading.
        parent = _parent_path(stub_path)
        return _parent_path(other_path) == stub_path or (
            parent != "" and _parent_path(other_path) == parent
        )

    def title(path: str) -> str:
        return path.rpartition(" > ")[2]

    def join(*parts: str) -> str:
        return "\n\n".join(p for p in parts if p)

    out: list[Section] = []
    merged = 0

    def settle(stub: tuple[str, str, str]) -> None:
        """Place a stub that has no following section to join: backward or alone."""
        nonlocal merged
        stub_path, stub_lead, stub_body = stub
        if out and eligible(stub_path, out[-1][0]):
            prev_path, prev_body = out[-1]
            out[-1] = (prev_path, join(prev_body, stub_lead, title(stub_path), stub_body))
            merged += 1
        else:
            out.append((stub_path, join(stub_lead, stub_body)))

    # A waiting stub: its own path and body, and the text of earlier stubs that
    # were merged into it and must stay in front of its own body.
    carry: tuple[str, str, str] | None = None
    for path, body in sections:
        lead = ""
        if carry is not None:
            stub_path, stub_lead, stub_body = carry
            if eligible(stub_path, path):
                # Under the stub's own heading the stub's title is already in the path.
                title_line = "" if path.startswith(f"{stub_path} > ") else title(stub_path)
                lead = join(stub_lead, title_line, stub_body)
                merged += 1
            else:
                settle(carry)
            carry = None
        if _approx_token_count(join(lead, body)) < MIN_SECTION_TOKENS:
            carry = (path, lead, body)
        else:
            out.append((path, join(lead, body)))
    if carry is not None:
        settle(carry)
    return out, merged


@dataclass
class NoiseCounts:
    sections: int
    link_blocks_dropped: int
    stubs_merged: int


def _clean_sections(body: str) -> tuple[list[Section], NoiseCounts]:
    sections = _split_by_headings(body) if body.strip() else []
    if not sections:
        sections = [("", body)]
    total = len(sections)
    sections, dropped = _drop_link_blocks(sections)
    sections, merged = _merge_stubs(sections)
    return sections, NoiseCounts(total, dropped, merged)


def noise_counts(content: str) -> NoiseCounts:
    """What chunking removes from ``content``, for the caller to log once."""
    return _clean_sections(split_frontmatter(content)[1])[1]


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
    sections, _ = _clean_sections(split_frontmatter(content)[1])

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
            child_texts = _split_by_size(
                parent_text, child_size, child_overlap, keep_paragraphs=True
            )
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
