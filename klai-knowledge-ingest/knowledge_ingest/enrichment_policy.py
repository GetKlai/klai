"""Shared policy for deciding when LLM enrichment should run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from knowledge_ingest.config import settings

_DEFAULT_MAX_CHUNKS = 200

# A markdown inline link: the "](" between label and target.
_MD_LINK = re.compile(r"\]\(")
# Sentence-ending punctuation followed by whitespace or end of text. Prose has
# these; a list of links does not.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

# Below this many links a page cannot be a navigation page, whatever its prose
# density — a two-link article would otherwise trip the ratio on a bad day.
_NAV_MIN_LINKS = 8
# Sentences per link under which the page is a link list rather than an
# article. Measured on the Voys corpus (2026-08-22):
#
#   https://help.voys.nl/                       2283 chars   34 links     0 sentences
#   https://help.voys.nl/2fa-freedom            2620 chars    2 links    12 sentences
#   https://help.voys.nl/aan-de-slag            4725 chars    6 links    26 sentences
#   https://help.voys.nl/yealink-dect-functies 27868 chars   32 links   143 sentences
#
# Link count alone does not separate them: the Yealink article has as many
# links as the index page. Prose does. Real articles run one sentence per
# ~180-220 characters; the index page has none at all.
_NAV_MAX_SENTENCES_PER_LINK = 0.25


def _configured_max_chunks() -> int:
    value = getattr(settings, "enrichment_max_chunks", _DEFAULT_MAX_CHUNKS)
    return value if isinstance(value, int) else _DEFAULT_MAX_CHUNKS


def enrichment_skip_reason(
    *,
    chunk_count: int,
    extra_payload: Mapping[str, Any] | None,
) -> str | None:
    """Return a machine-readable reason when LLM enrichment should be skipped."""
    extra = extra_payload or {}
    max_chunks = _configured_max_chunks()
    if extra.get("document_text_truncated"):
        docling_chunk_count = extra.get("docling_chunk_count")
        if docling_chunk_count is None:
            return "document_text_truncated"
        try:
            truncated_chunk_count = int(docling_chunk_count)
        except (TypeError, ValueError):
            return "document_text_truncated"
        if max_chunks > 0 and truncated_chunk_count > max_chunks:
            return "document_text_truncated"

    if max_chunks > 0 and chunk_count > max_chunks:
        return "too_many_chunks"

    return None


def graph_episode_skip_reason(document_text: str | None) -> str | None:
    """Return a reason when a document should NOT become a graph episode.

    A knowledge base contains pages that exist to point at other pages: index
    pages, category listings, tables of contents. They hold no facts about the
    world, only facts about the documentation, so extraction can only produce
    the meta-statements ``_EXTRACTION_INSTRUCTIONS`` rule 1 forbids
    (GetKlai/klai#1148). Replaying ``https://help.voys.nl/`` through extraction
    on 2026-08-22 returned nothing but "Een van de documentatieartikelen voor
    Freedom is getiteld 'Statistieken'" and its siblings.

    Skipping them is cheaper than teaching a prompt to reject them one edge at
    a time, and it is free: each episode costs roughly 26 LLM calls out of the
    shared klai-fast budget.

    This governs the GRAPH only. The page is still chunked, embedded and
    retrievable from Qdrant, which is where a link list belongs — someone
    searching for "overzicht" should still find it.
    """
    if not document_text:
        return None
    links = len(_MD_LINK.findall(document_text))
    if links < _NAV_MIN_LINKS:
        return None
    sentences = len(_SENTENCE_END.findall(document_text))
    if sentences < links * _NAV_MAX_SENTENCES_PER_LINK:
        return "navigation_page"
    return None
