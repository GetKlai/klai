"""Anchor texts reach the embedded text of a freshly enriched chunk as a "Linked as" line.

SPEC-CRAWLER-003 R9-R11, reworked: the crawler passes the filtered anchors in
``extra_payload["anchor_texts"]`` (link_graph.get_anchor_texts), and the cheap
layer, the same code kb_patch runs, writes the line. The enrichment step no
longer appends a raw "Also known as" block of its own.

Synthetic data only: invented names on example.com.
"""

from __future__ import annotations

import inspect

from knowledge_ingest import cheap_layer, enrichment_tasks
from knowledge_ingest.enrichment import EnrichedChunk

SOURCE = {"source_type": "crawl", "source_url": "https://help.example.com/orbit/install"}


def _chunk(text: str) -> EnrichedChunk:
    return EnrichedChunk(text, f"Context.\n\n{text}", "Context.", ["What is this?"])


def test_anchor_texts_become_a_linked_as_line_on_the_embedded_text_only():
    chunks = [_chunk("First paragraph."), _chunk("Second paragraph.")]
    extra = {**SOURCE, "anchor_texts": ["Softphone setup", "Fax inbox"]}

    cheap_layer.apply_to_enriched_chunks(chunks, extra, "orbit/install", [], [])

    for chunk, text in zip(chunks, ["First paragraph.", "Second paragraph."], strict=True):
        assert (
            chunk.enriched_text == f"Context.\n\n{text}\n\nLinked as: Softphone setup | Fax inbox"
        )
        assert chunk.original_text == text
        assert chunk.context_prefix == "Context."


def test_without_anchor_texts_the_embedded_text_is_unchanged():
    chunk = _chunk("First paragraph.")

    cheap_layer.apply_to_enriched_chunks([chunk], dict(SOURCE), "orbit/install", [], [])

    assert chunk.enriched_text == "Context.\n\nFirst paragraph."


def test_the_enrichment_step_builds_no_raw_anchor_block_of_its_own():
    assert "Also known as" not in inspect.getsource(enrichment_tasks._enrich_document)
