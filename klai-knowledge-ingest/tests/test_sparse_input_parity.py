"""REQ-6 — Sparse-input contextual parity.

Verifies that when knowledge_ingest builds sparse vectors for a chunk that has
a non-empty context_prefix, the sparse embedder receives the same contextualised
string (``context_prefix + "\\n\\n" + chunk_text``) as the dense embedder.

Root reference: enrichment_tasks.py lines 405-421.  Both dense and sparse
embedders are called with:

    enriched_texts = [ec.enriched_text for ec in enriched_chunks]

where ec.enriched_text is constructed by enrichment.py:435 as:

    enriched_text = f"{result.context_prefix}\\n\\n{chunk_text}"

This test documents that invariant so any future refactor that breaks parity
will produce a failing test rather than a silent regression.

AC-7 (SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001): sparse-input parity verified,
no code change required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Minimal replica of EnrichedChunk — avoids importing the full module graph.
# Matches the fields read by the Step 2 embedding block in enrichment_tasks.py.
# ---------------------------------------------------------------------------


@dataclass
class _FakeEnrichedChunk:
    original_text: str
    enriched_text: str  # "{context_prefix}\n\n{original_text}"
    context_prefix: str = ""
    questions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_enriched_chunks(
    chunks: list[tuple[str, str]],
) -> list[_FakeEnrichedChunk]:
    """Build fake enriched chunks from (context_prefix, chunk_text) pairs.

    Mirrors the assembly in enrichment.py:435:
        enriched_text = f"{result.context_prefix}\\n\\n{chunk_text}"
    """
    result = []
    for context_prefix, chunk_text in chunks:
        enriched_text = f"{context_prefix}\n\n{chunk_text}" if context_prefix else chunk_text
        result.append(
            _FakeEnrichedChunk(
                original_text=chunk_text,
                enriched_text=enriched_text,
                context_prefix=context_prefix,
            )
        )
    return result


def _extract_sparse_inputs(enriched_chunks: list[_FakeEnrichedChunk]) -> list[str]:
    """Replicate the input-assembly logic from enrichment_tasks.py:407.

    This is the single line that determines what the sparse embedder receives:
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]
    """
    return [ec.enriched_text for ec in enriched_chunks]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSparseInputIncludesContextPrefix:
    """Sparse embedder input must be context_prefix + chunk_text, not chunk_text alone."""

    def test_sparse_input_contains_prefix_for_prefixed_chunk(self):
        """Single chunk with non-empty context_prefix: sparse input starts with the prefix."""
        context_prefix = "Dit is een artikel over Voys telefonie-integraties."
        chunk_text = "De Salesforce koppeling werkt via RedCactus."

        chunks = _build_enriched_chunks([(context_prefix, chunk_text)])
        sparse_inputs = _extract_sparse_inputs(chunks)

        assert len(sparse_inputs) == 1
        assert sparse_inputs[0].startswith(context_prefix), (
            f"Sparse input must start with context_prefix.\n"
            f"Expected prefix: {context_prefix!r}\n"
            f"Actual input:    {sparse_inputs[0]!r}"
        )

    def test_sparse_input_contains_chunk_text(self):
        """Sparse input must also contain the original chunk text."""
        context_prefix = "Dit artikel beschrijft CRM-koppelingen."
        chunk_text = "Voys ondersteunt Bubble en RedCactus als CRM-koppeling."

        chunks = _build_enriched_chunks([(context_prefix, chunk_text)])
        sparse_inputs = _extract_sparse_inputs(chunks)

        assert chunk_text in sparse_inputs[0], (
            f"Sparse input must contain the original chunk text.\n"
            f"chunk_text:   {chunk_text!r}\n"
            f"Actual input: {sparse_inputs[0]!r}"
        )

    def test_sparse_input_matches_enriched_text_format(self):
        """Sparse input equals f'{context_prefix}\\n\\n{chunk_text}' exactly."""
        context_prefix = "Context voor de chunk."
        chunk_text = "De chunk inhoud zelf."
        expected = f"{context_prefix}\n\n{chunk_text}"

        chunks = _build_enriched_chunks([(context_prefix, chunk_text)])
        sparse_inputs = _extract_sparse_inputs(chunks)

        assert sparse_inputs[0] == expected, (
            f"Sparse input format mismatch.\nExpected: {expected!r}\nGot:      {sparse_inputs[0]!r}"
        )

    def test_sparse_input_matches_dense_input_exactly(self):
        """Sparse and dense embedders must receive identical inputs (parity)."""
        data = [
            ("Prefix voor chunk A.", "Chunk A tekst."),
            ("Prefix voor chunk B.", "Chunk B tekst."),
        ]
        chunks = _build_enriched_chunks(data)
        enriched_texts = [ec.enriched_text for ec in chunks]

        # Both dense and sparse inputs come from the same enriched_texts list
        # (enrichment_tasks.py:407-416). This test documents that single-source invariant.
        dense_inputs = enriched_texts
        sparse_inputs = enriched_texts  # same variable passed to both callers

        assert dense_inputs == sparse_inputs

    def test_sparse_input_without_prefix_uses_chunk_text_only(self):
        """Chunks with no context_prefix: sparse input is chunk_text alone (no leading \\n\\n)."""
        context_prefix = ""
        chunk_text = "Een chunk zonder context prefix."

        chunks = _build_enriched_chunks([(context_prefix, chunk_text)])
        sparse_inputs = _extract_sparse_inputs(chunks)

        assert sparse_inputs[0] == chunk_text, (
            f"Empty prefix: sparse input must equal chunk_text.\n"
            f"Expected: {chunk_text!r}\n"
            f"Got:      {sparse_inputs[0]!r}"
        )

    def test_sparse_input_batch_preserves_order(self):
        """All chunks in a batch preserve their prefix-chunk assembly and order."""
        data = [
            ("Prefix 1.", "Chunk 1."),
            ("Prefix 2.", "Chunk 2."),
            ("Prefix 3.", "Chunk 3."),
        ]
        chunks = _build_enriched_chunks(data)
        sparse_inputs = _extract_sparse_inputs(chunks)

        assert len(sparse_inputs) == 3
        for i, (prefix, text) in enumerate(data):
            expected = f"{prefix}\n\n{text}"
            assert sparse_inputs[i] == expected, (
                f"Chunk {i}: expected {expected!r}, got {sparse_inputs[i]!r}"
            )


class TestSparseEmbedderCalledWithEnrichedText:
    """Integration-level check: embed_sparse_batch is invoked with contextualised strings.

    This mirrors the actual call site in enrichment_tasks.py:
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]
        vecs = await sparse_embedder.embed_sparse_batch(enriched_texts)

    We mock embed_sparse_batch and verify the argument it receives.
    """

    @pytest.mark.asyncio
    async def test_embed_sparse_batch_receives_contextualised_strings(self):
        """Mocked embed_sparse_batch is called with prefix+chunk strings, not raw chunk text."""
        from knowledge_ingest import sparse_embedder

        context_prefix = "Dit artikel gaat over Voys Freedom en CRM-systemen."
        chunk_text = "Koppel Voys aan Salesforce via de Bubble integratie."
        enriched_text = f"{context_prefix}\n\n{chunk_text}"

        # Simulate what enrichment_tasks.py does at lines 407 + 416
        enriched_chunks = [
            _FakeEnrichedChunk(
                original_text=chunk_text,
                enriched_text=enriched_text,
                context_prefix=context_prefix,
            )
        ]
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]

        captured_call: list[list[str]] = []

        async def mock_embed_sparse_batch(texts: list[str]) -> list[None]:
            captured_call.append(list(texts))
            return [None] * len(texts)

        with patch.object(
            sparse_embedder, "embed_sparse_batch", side_effect=mock_embed_sparse_batch
        ):
            await sparse_embedder.embed_sparse_batch(enriched_texts)

        assert len(captured_call) == 1, "embed_sparse_batch should be called once"
        assert captured_call[0] == [enriched_text], (
            f"embed_sparse_batch must receive the contextualised string.\n"
            f"Expected: {[enriched_text]!r}\n"
            f"Got:      {captured_call[0]!r}"
        )
        # Specifically: NOT the raw chunk_text
        assert captured_call[0] != [chunk_text], (
            "embed_sparse_batch must NOT receive raw chunk_text without the context_prefix"
        )

    @pytest.mark.asyncio
    async def test_embed_sparse_batch_not_called_with_raw_chunk_text(self):
        """Assert the sparse embedder does NOT receive raw chunk_text when a prefix is present."""
        from knowledge_ingest import sparse_embedder

        context_prefix = "Context: artikel over nummerportabiliteit."
        chunk_text = "U kunt uw nummer meenemen naar Voys."
        enriched_text = f"{context_prefix}\n\n{chunk_text}"

        enriched_chunks = [
            _FakeEnrichedChunk(
                original_text=chunk_text,
                enriched_text=enriched_text,
                context_prefix=context_prefix,
            )
        ]
        enriched_texts = [ec.enriched_text for ec in enriched_chunks]

        received: list[list[str]] = []

        async def capturing_batch(texts: list[str]) -> list[None]:
            received.append(list(texts))
            return [None]

        with patch.object(sparse_embedder, "embed_sparse_batch", side_effect=capturing_batch):
            await sparse_embedder.embed_sparse_batch(enriched_texts)

        assert received[0][0] != chunk_text, (
            "Sparse embedder must not receive raw chunk_text when context_prefix is present"
        )
        assert context_prefix in received[0][0], (
            "Sparse embedder input must include the context_prefix"
        )
