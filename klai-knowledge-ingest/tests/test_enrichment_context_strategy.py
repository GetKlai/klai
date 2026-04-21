"""Tests that context strategies are correctly applied in enrich_chunks."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.enrichment import enrich_chunks


DOCUMENT = " ".join([f"word{i}" for i in range(500)])  # ~500 words, long enough for all strategies


def _fake_enrich_chunk_factory(captured: list) -> AsyncMock:
    """Return an AsyncMock that records the context_window argument each call receives."""

    async def _fake(document_text, chunk_text, title, path, *, question_focus="", participant_context="", context_window=None, **kwargs):
        captured.append(context_window)
        return MagicMock(context_prefix="prefix", content_type="conceptual", questions=["q?"])

    return _fake


@pytest.mark.asyncio
async def test_first_n_strategy_uses_start_of_document():
    """first_n strategy: context window should start from the beginning of the document."""
    captured: list = []

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=_fake_enrich_chunk_factory(captured)):
        await enrich_chunks(
            document_text=DOCUMENT,
            chunks=["chunk A", "chunk B"],
            title="title",
            path="path.md",
            context_strategy="first_n",
            context_tokens=50,
        )

    assert len(captured) == 2
    # first_n returns doc[:n*4] — should be identical for every chunk
    assert captured[0] == captured[1]
    # Must start at the beginning of the document
    assert captured[0] is not None
    assert captured[0].startswith("word0")


@pytest.mark.asyncio
async def test_rolling_window_strategy_differs_per_chunk():
    """rolling_window strategy: context window should differ for different chunk positions."""
    captured: list = []

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=_fake_enrich_chunk_factory(captured)):
        await enrich_chunks(
            document_text=DOCUMENT,
            chunks=["chunk A", "chunk B", "chunk C", "chunk D", "chunk E"],
            title="title",
            path="path.md",
            context_strategy="rolling_window",
            context_tokens=50,
        )

    assert len(captured) == 5
    # With different chunk_index values, not all windows should be identical
    # (rolling_window shifts by ~50 words per chunk_index)
    assert not all(c == captured[0] for c in captured), (
        "rolling_window should produce different context for different chunk indices"
    )


@pytest.mark.asyncio
async def test_most_recent_strategy_uses_end_of_document():
    """most_recent strategy: context window should come from the end of the document."""
    captured: list = []
    doc = "start " * 100 + "end_content"

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=_fake_enrich_chunk_factory(captured)):
        await enrich_chunks(
            document_text=doc,
            chunks=["chunk A"],
            title="title",
            path="path.md",
            context_strategy="most_recent",
            context_tokens=10,
        )

    assert len(captured) == 1
    assert captured[0] is not None
    # most_recent takes the last n*4 chars — should contain the end of the document
    assert "end_content" in captured[0]


@pytest.mark.asyncio
async def test_unknown_strategy_falls_back_to_first_n():
    """Unknown strategy name falls back to first_n (via STRATEGIES.get default)."""
    captured: list = []

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=_fake_enrich_chunk_factory(captured)):
        await enrich_chunks(
            document_text=DOCUMENT,
            chunks=["chunk A"],
            title="title",
            path="path.md",
            context_strategy="nonexistent_strategy",
            context_tokens=50,
        )

    assert len(captured) == 1
    assert captured[0] is not None
    # Falls back to first_n — starts from the beginning
    assert captured[0].startswith("word0")


@pytest.mark.asyncio
async def test_context_window_passed_to_enrich_chunk():
    """Verify that enrich_chunk receives context_window (not None) when strategy is active."""
    captured: list = []

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=_fake_enrich_chunk_factory(captured)):
        await enrich_chunks(
            document_text="hello world " * 100,
            chunks=["chunk"],
            title="title",
            path="path.md",
            context_strategy="first_n",
            context_tokens=20,
        )

    assert len(captured) == 1
    assert captured[0] is not None
    assert isinstance(captured[0], str)
    assert len(captured[0]) <= 20 * 4  # context_tokens * 4 chars/token
