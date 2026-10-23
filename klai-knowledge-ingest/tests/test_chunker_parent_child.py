"""Tests for SPEC-RAG-PARENT-CHILD-001 chunk_markdown_with_parents."""

from __future__ import annotations

import json

from knowledge_ingest.chunker import (
    CHILD_CHUNK_SIZE_DEFAULT,
    PARENT_CHUNK_SIZE_DEFAULT,
    Chunk,
    ParentChunk,
    chunk_markdown_with_parents,
)
from knowledge_ingest.document_normalizer import normalize_document_for_chunking

# A short document that fits in one parent — exercises the trivial case.
_SHORT_DOC = """\
# Bubble troubleshoot

Wanneer Bubble vastloopt, herstart de plugin via Chrome's extension menu.
Als het probleem aanhoudt, controleer of de microfoon-permissie nog actief is.
Werkt het dan nog niet, leg de stappen vast in een ticket en escaleer naar Voys CS.
"""


# A longer document with multiple headings — exercises section boundaries
# and child fan-out within a parent.
_LONG_DOC = (
    "# Customer service flows\n\n"
    + "Belangrijke procedures voor het CS-team. " * 20
    + "\n\n## Uitportering\n\n"
    + "De uitportering wordt aangevraagd via Freedom. " * 30
    + "Volg de volgende stappen om de portering correct af te handelen. " * 30
    + "\n\n## Heractivering\n\n"
    + "Een heractivering volgt de standaard CS-flow. " * 20
    + "\n\n## Opzegging\n\n"
    + "Opzeggingen worden binnen 24 uur verwerkt. " * 20
)


def test_short_doc_yields_single_parent_with_one_or_more_children() -> None:
    children, parents = chunk_markdown_with_parents(_SHORT_DOC)

    assert len(parents) == 1
    assert len(children) >= 1
    # All children point at the same parent
    assert all(c.parent_index == 0 for c in children)
    # Parent records every child index
    assert sorted(parents[0].child_indices) == list(range(len(children)))


def test_long_doc_yields_one_parent_per_heading_section() -> None:
    """Each markdown ## section gets its own parent (when it fits)."""
    _, parents = chunk_markdown_with_parents(_LONG_DOC)

    headings = {p.heading_path for p in parents}
    assert "Customer service flows > Uitportering" in headings
    assert "Customer service flows > Heractivering" in headings
    assert "Customer service flows > Opzegging" in headings


def test_each_child_has_valid_parent_index() -> None:
    children, parents = chunk_markdown_with_parents(_LONG_DOC)
    assert len(children) > 0
    for c in children:
        assert isinstance(c, Chunk)
        assert c.parent_index is not None
        assert 0 <= c.parent_index < len(parents)


def test_parent_child_indices_are_consistent() -> None:
    """Every child_index claimed by a parent maps back to that parent."""
    children, parents = chunk_markdown_with_parents(_LONG_DOC)
    for parent_idx, parent in enumerate(parents):
        assert isinstance(parent, ParentChunk)
        for child_idx in parent.child_indices:
            assert children[child_idx].parent_index == parent_idx


def test_children_smaller_than_default_size() -> None:
    """Child chunks stay roughly within the configured child_size."""
    children, _ = chunk_markdown_with_parents(_LONG_DOC)
    for c in children:
        # Allow a bit of slack for the heading-prefix on display text.
        assert len(c.text) <= CHILD_CHUNK_SIZE_DEFAULT + 200


def test_parents_no_larger_than_parent_size_with_heading_prefix() -> None:
    """Parents stay within parent_size + heading-prefix overhead."""
    _, parents = chunk_markdown_with_parents(_LONG_DOC)
    for p in parents:
        assert len(p.text) <= PARENT_CHUNK_SIZE_DEFAULT + 200


def test_empty_input_returns_empty_lists() -> None:
    children, parents = chunk_markdown_with_parents("")
    assert children == []
    assert parents == []


def test_no_heading_doc_falls_back_to_single_section() -> None:
    """A document without markdown headings still produces parents/children."""
    plain = "Geen heading. " * 200
    children, parents = chunk_markdown_with_parents(plain)
    assert len(parents) >= 1
    assert len(children) >= 1
    # All parents have empty heading_path
    assert all(p.heading_path == "" for p in parents)


def test_frontmatter_is_stripped_from_chunks() -> None:
    """YAML frontmatter must not appear in any child or parent text."""
    doc = ("---\ntitle: Test\ntags: [crm, voys]\n---\n\n# Visible heading\n\n") + (
        "Visible body content. " * 30
    )
    children, parents = chunk_markdown_with_parents(doc)
    for c in children:
        assert "title: Test" not in c.text
        assert "tags: [crm, voys]" not in c.text
    for p in parents:
        assert "title: Test" not in p.text


def test_blocknote_json_is_normalized_before_chunking() -> None:
    blocks = [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "User management lives under ", "styles": {}},
                {"type": "text", "text": "Admin > Users", "styles": {"bold": True}},
                {"type": "text", "text": ".", "styles": {}},
            ],
            "children": [],
        },
        {
            "type": "heading",
            "props": {"level": 2},
            "content": [{"type": "text", "text": "Invite a colleague", "styles": {}}],
            "children": [],
        },
        {
            "type": "numberedListItem",
            "content": [{"type": "text", "text": "Click Invite.", "styles": {}}],
            "children": [],
        },
    ]
    content = f"---\ntitle: Invite people\n---\n\n{json.dumps(blocks)}"

    normalized = normalize_document_for_chunking(content)
    children, parents = chunk_markdown_with_parents(normalized)
    combined = "\n\n".join([*(c.text for c in children), *(p.text for p in parents)])

    assert "## Invite a colleague" in normalized
    assert "User management lives under **Admin > Users**." in combined
    assert "Invite a colleague" in combined
    assert "1. Click Invite." in combined
    assert '"type":"paragraph"' not in combined


def test_position_field_is_zero_indexed_and_monotonic() -> None:
    _, parents = chunk_markdown_with_parents(_LONG_DOC)
    positions = [p.position for p in parents]
    assert positions == list(range(len(parents)))


def test_parent_text_contains_all_child_content() -> None:
    """Every child's body content (minus heading prefix) appears in its parent."""
    children, parents = chunk_markdown_with_parents(_LONG_DOC)
    for c in children:
        parent = parents[c.parent_index]  # type: ignore[index]
        # Strip the heading prefix from both for the comparison.
        child_body = c.text.split("\n\n", 1)[-1] if "\n\n" in c.text else c.text
        parent_body = parent.text.split("\n\n", 1)[-1] if "\n\n" in parent.text else parent.text
        # First 50 chars of the child body must appear somewhere in the parent.
        sample = child_body[:50].strip()
        if sample:
            assert sample in parent_body, f"Child fragment {sample!r} not found in parent text"


def _words(text: str) -> str:
    return "".join(text.split())


def test_children_do_not_overlap_and_cover_the_parent() -> None:
    """Children are cut end to end: every character of the parent is in exactly one child."""
    sentences = " ".join(f"Sentence number {i} explains one step of the flow." for i in range(80))
    doc = f"# Flow\n\n{sentences}"
    children, parents = chunk_markdown_with_parents(doc)

    assert len(parents) == 1
    assert len(children) > 3
    heading_len = len("Flow\n\n")
    bodies = [c.text[heading_len:] for c in children]
    assert _words("".join(bodies)) == _words(parents[0].text[heading_len:])


def test_children_stay_in_the_200_to_300_token_range() -> None:
    sentences = " ".join(f"Sentence number {i} explains one step of the flow." for i in range(100))
    children, _ = chunk_markdown_with_parents(f"# Flow\n\n{sentences}")

    sizes = [len(c.text) // 4 for c in children[:-1]]  # the last one is the remainder
    assert sizes
    assert all(150 <= s <= 300 for s in sizes), sizes


def test_a_paragraph_that_fits_in_a_child_is_not_cut() -> None:
    """A short paragraph before a 900-char one must not pull the long one apart."""
    short = ("Short lead paragraph of the section. " * 6).strip()
    long_par = "Long paragraph that is cut only if the chunker ignores paragraph limits. " * 12
    long_par = long_par.strip()[:900]
    children, _ = chunk_markdown_with_parents(f"# T\n\n{short}\n\n{long_par}")

    assert sum(long_par in c.text for c in children) == 1


def _sentences(chars: int) -> str:
    """Exactly ``chars`` characters of short sentences, so a sentence end is always near."""
    out, i = "", 0
    while len(out) < chars:
        out += f"Step number {i:03d} is done before the next one starts. "
        i += 1
    return out[:chars]


def test_splitting_with_overlap_terminates_when_a_short_lead_precedes_a_paragraph() -> None:
    """The early paragraph break must move the window forward even when overlap > 0."""
    import signal

    from knowledge_ingest.chunker import _split_by_size

    text = "x" * 59 + "\n\n" + "y" * 979

    def on_alarm(*_: object) -> None:
        raise TimeoutError("_split_by_size did not terminate")

    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(5)
    try:
        chunks = _split_by_size(text, 1000, 200, keep_paragraphs=True)
    finally:
        signal.alarm(0)

    assert len(chunks) >= 2 and sum(c.count("y") for c in chunks) >= 979


def test_a_short_lead_does_not_become_its_own_parent() -> None:
    lead = _sentences(131)
    body = _sentences(5939)
    _, parents = chunk_markdown_with_parents(f"# T\n\n{lead}\n\n{body}")

    assert len(parents) <= 2
    assert len(parents[0].text) > 3000


def test_a_short_lead_stays_with_the_paragraph_after_it_in_the_child_split() -> None:
    lead = "One line of lead text."
    body = _sentences(979)
    children, _ = chunk_markdown_with_parents(f"# T\n\n{lead}\n\n{body}")

    assert lead in children[0].text
    assert len(children[0].text) > 500
