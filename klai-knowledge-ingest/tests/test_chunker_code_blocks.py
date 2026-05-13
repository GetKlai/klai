"""Tests for audit-2026-05-06 finding 8: chunker code-block awareness.

Pin the contract:
- ``_find_code_block_ranges`` recognises ``` and ~~~ fences, 3+ markers,
  closer must match opener char + length, unterminated fences extend
  to end-of-text.
- ``_split_by_headings`` skips heading-shaped lines inside fenced
  blocks.
- Real headings outside code blocks are still detected.
"""

from __future__ import annotations

from knowledge_ingest.chunker import (
    _find_code_block_ranges,
    _split_by_headings,
    chunk_markdown,
    chunk_markdown_with_parents,
)

# ---------------------------------------------------------------------------
# _find_code_block_ranges
# ---------------------------------------------------------------------------


def test_no_fences_returns_empty_list():
    assert _find_code_block_ranges("# heading\n\nsome text\nmore text") == []


def test_single_backtick_fenced_block():
    text = "before\n```python\nx = 1\n# this is a comment\n```\nafter"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1
    start, end = ranges[0]
    assert text[start:].startswith("```python")
    assert text[start:end].endswith("```")


def test_tilde_fenced_block():
    text = "before\n~~~\ndata\n# also not a heading\n~~~\nafter"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1


def test_mismatched_fence_chars_not_treated_as_close():
    """``` cannot close ~~~ and vice versa — the opener stays open
    until a matching closer or end-of-text.
    """
    text = "before\n```py\ncontent\n~~~\nstill in code\n```\nafter"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1, (
        "the ``` opener must only close on another ``` line; the ~~~ in between is just code body"
    )
    # The single range should span from ``` to ```
    start, end = ranges[0]
    assert text[start : start + 5] == "```py"
    assert text[end - 3 : end] == "```"


def test_unterminated_fence_extends_to_end_of_text():
    text = "intro\n```\nthis block never closes"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1
    _start, end = ranges[0]
    assert end == len(text)


def test_closer_can_have_more_markers_than_opener():
    """CommonMark: closer must be at least as long, may be longer."""
    text = "before\n```\nbody\n`````\nafter"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1
    _start, end = ranges[0]
    # Verify the close-fence (5 backticks) was used as terminator
    assert text[end - 5 : end] == "`````"


def test_closer_with_fewer_markers_does_not_close():
    """A 3-backtick closer cannot close a 4-backtick opener."""
    text = "before\n````\nbody\n```\nstill in code\n````\nafter"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 1
    _start, end = ranges[0]
    # The 4-backtick line at the end must be the actual closer
    assert text[end - 4 : end] == "````"


def test_multiple_code_blocks_each_get_their_own_range():
    text = "intro\n```\nblock 1\n```\nbetween\n```\nblock 2\n```\noutro"
    ranges = _find_code_block_ranges(text)
    assert len(ranges) == 2


# ---------------------------------------------------------------------------
# _split_by_headings — code blocks suppress heading detection
# ---------------------------------------------------------------------------


def test_python_comment_in_code_block_not_treated_as_heading():
    """The exact failure case from finding 8."""
    text = (
        "# Real H1\n"
        "\n"
        "Some prose here.\n"
        "\n"
        "```python\n"
        "# this is a Python comment, NOT a heading\n"
        "def foo():\n"
        "    pass\n"
        "```\n"
        "\n"
        "More prose.\n"
    )
    sections = _split_by_headings(text)

    # Exactly one heading detected: the real H1
    heading_paths = [hp for hp, _body in sections]
    assert heading_paths == ["Real H1"], (
        f"Expected only 'Real H1', got {heading_paths}. "
        f"The Python comment '# this is a Python comment...' inside the "
        f"code block leaked into the heading detector."
    )


def test_shebang_in_code_block_not_treated_as_heading():
    text = (
        "# Setup script\n"
        "\n"
        "Run the install:\n"
        "\n"
        "```bash\n"
        "#!/usr/bin/env bash\n"
        "# install dependencies\n"
        "apt-get install foo\n"
        "```\n"
    )
    sections = _split_by_headings(text)
    heading_paths = [hp for hp, _body in sections]
    assert heading_paths == ["Setup script"]


def test_markdown_heading_inside_code_block_not_treated_as_heading():
    """A literal markdown heading INSIDE a fenced block (e.g. when the
    code block is teaching markdown syntax) must stay inside the block.
    """
    text = (
        "# Real H1\n"
        "\n"
        "Here is markdown syntax for headings:\n"
        "\n"
        "~~~markdown\n"
        "# H1 inside example\n"
        "## H2 inside example\n"
        "Some example body.\n"
        "~~~\n"
        "\n"
        "## Real H2 after the example\n"
        "Body of real H2.\n"
    )
    sections = _split_by_headings(text)
    heading_paths = [hp for hp, _body in sections]
    assert heading_paths == [
        "Real H1",
        "Real H1 > Real H2 after the example",
    ], (
        f"Expected only the two real headings, got {heading_paths}. "
        f"The example markdown block leaked through."
    )


def test_real_headings_around_code_blocks_still_detected():
    """Don't break the happy path — code-block awareness must not hide
    real headings before or after a fenced block.
    """
    text = "# H1 before\nBody before.\n\n```python\nx = 1\n```\n\n## H2 after\nBody after.\n"
    sections = _split_by_headings(text)
    heading_paths = [hp for hp, _body in sections]
    assert "H1 before" in heading_paths
    assert "H1 before > H2 after" in heading_paths


def test_unterminated_code_block_swallows_following_headings():
    """An opened fence with no closer means the rest of the document
    is code body. Any heading-shaped lines after it are inside the
    block. This matches CommonMark behavior.
    """
    text = (
        "# Real H1\n"
        "\n"
        "Some prose.\n"
        "\n"
        "```python\n"
        "# this looks like a heading but the fence is open\n"
        "## another fake heading\n"
        "def f(): pass\n"
    )
    sections = _split_by_headings(text)
    heading_paths = [hp for hp, _body in sections]
    assert heading_paths == ["Real H1"]


# ---------------------------------------------------------------------------
# Public chunker entry points still work end-to-end
# ---------------------------------------------------------------------------


def test_chunk_markdown_does_not_emit_code_comment_as_heading_path():
    """End-to-end: chunk_markdown's child Chunks must not have
    code-comment text in heading_path.
    """
    text = (
        "# Voys onboarding\n"
        "\n"
        "Run the setup script:\n"
        "\n"
        "```bash\n"
        "# step 1: install\n"
        "apt-get install klai\n"
        "# step 2: configure\n"
        "klai config\n"
        "```\n"
        "\n"
        "Then verify the install.\n"
    )
    chunks = chunk_markdown(text, chunk_size=5000, overlap=200)
    heading_paths = {c.heading_path for c in chunks}
    # The only heading path should be the real H1
    assert heading_paths == {"Voys onboarding"}, f"chunk heading_paths leaked: {heading_paths}"


def test_chunk_markdown_with_parents_does_not_emit_code_comment_as_heading():
    """Parent/child chunker must inherit the same protection."""
    text = (
        "# Real H1\n"
        "\n"
        "```python\n"
        "# fake H1 in code\n"
        "## fake H2 in code\n"
        "def f(): pass\n"
        "```\n"
        "\n"
        "## Real H2\n"
        "Body of H2.\n"
    )
    children, parents = chunk_markdown_with_parents(text)
    child_heading_paths = {c.heading_path for c in children}
    parent_heading_paths = {p.heading_path for p in parents}
    # Only the real H1 and Real H1 > Real H2 should appear
    assert child_heading_paths == {"Real H1", "Real H1 > Real H2"}
    assert parent_heading_paths == {"Real H1", "Real H1 > Real H2"}
