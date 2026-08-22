"""A graph backfill must not silently drop most of the corpus.

The original one-shot capped episode text at 4000 characters to "reduce LLM
calls". Measured on the Voys Dutch corpus on 2026-08-22: 488 of 726 documents
exceed that, and only 36% of 6.59M characters ever reached extraction. The
graph therefore had no facts about the second half of two documents in three,
and nothing recorded that it had happened.
"""

from __future__ import annotations

from knowledge_ingest import backfill


def test_cap_retains_the_bulk_of_a_real_corpus():
    """4000 retained 36% of the measured corpus; 30000 retains 91%."""
    assert backfill.MAX_TEXT_CHARS >= 30000, (
        "a cap this low drops most of every average document -- measured, "
        "488 of 726 Voys documents exceed 4000 characters"
    )


def test_cap_still_exists():
    """Not unbounded: one Voys artifact is 464k characters.

    Removing the limit entirely would let a single document exhaust the context
    window and a meaningful slice of the shared per-tenant rate budget.
    """
    assert backfill.MAX_TEXT_CHARS is not None
    assert backfill.MAX_TEXT_CHARS <= 60000


def test_truncation_is_reported(caplog):
    """Silent truncation is what made this invisible for five months."""
    import inspect

    source = inspect.getsource(backfill)
    truncation_block = source[source.index("MAX_TEXT_CHARS:") :]
    truncation_block = truncation_block[: truncation_block.index("try:")]

    assert "log.warning" in truncation_block, (
        "truncation leaves no trace -- a document loses its tail and nothing says so"
    )
    assert "TRUNCATED" in truncation_block
