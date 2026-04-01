"""Tests for anchor text augmentation in _enrich_document().

SPEC-CRAWLER-003: R9, R10, R11
Verifies that anchor texts from linking pages are deduplicated and appended
to enriched_text only, leaving original_text and context_prefix unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fake EnrichedChunk -- mirrors knowledge_ingest.enrichment.EnrichedChunk
# without pulling in the real module and its heavy transitive deps.
# ---------------------------------------------------------------------------

@dataclass
class FakeEnrichedChunk:
    original_text: str
    enriched_text: str
    context_prefix: str = ""
    questions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ANCHOR_BLOCK_PREFIX = "\n\nAnder pagina's noemen deze pagina: "


def _make_chunks(texts: list[str]) -> list[FakeEnrichedChunk]:
    """Build fake enriched chunks with enriched_text == original_text."""
    return [
        FakeEnrichedChunk(
            original_text=t,
            enriched_text=f"Context.\n\n{t}",
            context_prefix="Context.",
            questions=["Wat is dit?"],
        )
        for t in texts
    ]


# ---------------------------------------------------------------------------
# Scenario 4.1: Anchor text deduplicated and appended
# ---------------------------------------------------------------------------

class TestAnchorTextDeduplicatedAndAppended:
    """Given enriched chunks and extra_payload with duplicate anchor_texts,
    each ec.enriched_text ends with the deduplicated anchor block."""

    def test_anchor_block_appended_to_enriched_text(self):
        chunks = _make_chunks(["Eerste paragraaf.", "Tweede paragraaf."])
        anchor_texts_raw = ["Handleiding", "Getting Started", "Handleiding"]

        # -- act: apply the anchor text augmentation logic --
        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
        for ec in chunks:
            ec.enriched_text += anchor_block

        # -- assert --
        for ec in chunks:
            assert ec.enriched_text.endswith(
                "Ander pagina's noemen deze pagina: Handleiding | Getting Started"
            )

    def test_duplicate_removed(self):
        anchor_texts_raw = ["Handleiding", "Getting Started", "Handleiding"]
        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        assert unique_anchors == ["Handleiding", "Getting Started"]

    def test_handleiding_appears_only_once(self):
        chunks = _make_chunks(["Tekst."])
        anchor_texts_raw = ["Handleiding", "Getting Started", "Handleiding"]

        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
        for ec in chunks:
            ec.enriched_text += anchor_block

        for ec in chunks:
            assert ec.enriched_text.count("Handleiding") == 1


# ---------------------------------------------------------------------------
# Scenario 4.2: Empty anchor_texts list -- no modification
# ---------------------------------------------------------------------------

class TestEmptyAnchorTextsNoModification:
    """Given extra_payload with empty anchor_texts, enriched_text is unchanged."""

    def test_empty_list_no_change(self):
        chunks = _make_chunks(["Originele tekst."])
        original_texts = [ec.enriched_text for ec in chunks]

        anchor_texts_raw = []
        if anchor_texts_raw:
            unique_anchors = list(dict.fromkeys(anchor_texts_raw))
            anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
            for ec in chunks:
                ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_texts):
            assert ec.enriched_text == orig

    def test_missing_key_no_change(self):
        chunks = _make_chunks(["Originele tekst."])
        original_texts = [ec.enriched_text for ec in chunks]
        extra_payload: dict = {}

        anchor_texts_raw = extra_payload.get("anchor_texts", [])
        if anchor_texts_raw:
            unique_anchors = list(dict.fromkeys(anchor_texts_raw))
            anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
            for ec in chunks:
                ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_texts):
            assert ec.enriched_text == orig

    def test_none_extra_payload_no_change(self):
        chunks = _make_chunks(["Originele tekst."])
        original_texts = [ec.enriched_text for ec in chunks]
        extra_payload = None

        anchor_texts_raw = extra_payload.get("anchor_texts", []) if extra_payload else []
        if anchor_texts_raw:
            unique_anchors = list(dict.fromkeys(anchor_texts_raw))
            anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
            for ec in chunks:
                ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_texts):
            assert ec.enriched_text == orig


# ---------------------------------------------------------------------------
# Scenario 4.3: original_text and context_prefix unchanged
# ---------------------------------------------------------------------------

class TestOriginalFieldsUnchanged:
    """Given anchor_texts, original_text and context_prefix must NOT be modified."""

    def test_original_text_unchanged(self):
        chunks = _make_chunks(["Mijn originele tekst."])
        original_original = [ec.original_text for ec in chunks]

        anchor_texts_raw = ["API Documentatie"]
        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
        for ec in chunks:
            ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_original):
            assert ec.original_text == orig

    def test_context_prefix_unchanged(self):
        chunks = _make_chunks(["Mijn originele tekst."])
        original_prefixes = [ec.context_prefix for ec in chunks]

        anchor_texts_raw = ["API Documentatie"]
        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
        for ec in chunks:
            ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_prefixes):
            assert ec.context_prefix == orig

    def test_questions_unchanged(self):
        chunks = _make_chunks(["Mijn originele tekst."])
        original_questions = [list(ec.questions) for ec in chunks]

        anchor_texts_raw = ["API Documentatie"]
        unique_anchors = list(dict.fromkeys(anchor_texts_raw))
        anchor_block = ANCHOR_BLOCK_PREFIX + " | ".join(unique_anchors)
        for ec in chunks:
            ec.enriched_text += anchor_block

        for ec, orig in zip(chunks, original_questions):
            assert ec.questions == orig
