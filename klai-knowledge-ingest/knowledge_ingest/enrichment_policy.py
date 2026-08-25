"""Shared policy for deciding when LLM enrichment should run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from knowledge_ingest.config import settings

_DEFAULT_MAX_CHUNKS = 200

# Version 1 is the implicit, never-stamped legacy ruleset from before #1148.
# Version 2 is the subject-facts, navigation-page skip, cross-language entity
# naming ruleset used for the 2026-08-22 Voys rebuild. Bump this whenever the
# extraction prompt in graph.py or the skip rules below change semantically.
GRAPHITI_EXTRACTION_VERSION = 2


def _configured_max_chunks() -> int:
    value = getattr(settings, "enrichment_max_chunks", _DEFAULT_MAX_CHUNKS)
    return value if isinstance(value, int) else _DEFAULT_MAX_CHUNKS


# Markdown image, then markdown link. Images first: "![alt](src)" also ends in
# "](", so a link pattern alone counts every screenshot as a link, and an
# illustrated walkthrough with terse captions looks exactly like a link list.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
# List bullets and whitespace are not prose either.
_LIST_FURNITURE = re.compile(r"(?m)^[\s>*\-+#|]+")

# Below this many links a page cannot be a navigation page whatever its prose
# density — a two-link stub would otherwise trip on a terse day.
_NAV_MIN_LINKS = 8
# Characters of prose PER LINK, counting only what is left once every link,
# image and list bullet is removed. A page that points at other pages has
# almost nothing here, because its entire text lives inside link labels.
#
# Per link rather than an absolute floor: a big index carries more incidental
# text than a small one purely by being big, so a fixed threshold lets the
# largest — and worst — index pages through. Measured on the Voys corpus
# (2026-08-22):
#
#   redcactus.cloud/nl/phone-software    90 links     252 prose      2.8 per link
#   help.voys.nl/                        34 links     130 prose      3.8 per link
#   redcactus.cloud/nl/crm-software     211 links     972 prose      4.6 per link
#   voys.nl/algemene-voorwaarden/       125 links    1231 prose      9.8 per link
#   help.voys.nl/integraties             12 links     323 prose     26.9 per link
#   help.voys.nl/aan-de-slag              6 links    3573 prose    595.5 per link
#   help.voys.nl/yealink-dect-functies   32 links   19654 prose    614.2 per link
#
# Index pages sit under 10, real articles over 500 — two orders of magnitude
# apart. 40 sits in the empty middle with a 15x margin to the nearest article,
# which is the margin worth having: a false positive silently removes a real
# document from the graph, and nobody would notice.
#
# Neither link count nor sentence count survives on its own. yealink-dect-
# functies carries as many links as the index page, so counting links throws
# out one of the richest documents in the corpus. Counting sentence
# punctuation breaks the other way: a list written "* [Article](url)." scores
# one sentence per link and slips through, while a language whose sentences do
# not end in ASCII "." scores zero and is discarded. The question is "does this
# page contain writing", so measure the writing.
_NAV_MAX_PROSE_PER_LINK = 40


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
    Freedom is getiteld 'Statistieken'" and eleven siblings.

    Skipping them is cheaper than teaching a prompt to reject them one edge at
    a time, and it is not free to get wrong in either direction: each episode
    costs roughly 26 LLM calls out of the shared klai-fast budget, and a false
    positive silently removes a real article from the graph.

    Callers must apply this themselves — ``routes/ingest.py`` for live ingest
    and ``backfill.py`` for the operator one-shot. It is deliberately not
    hidden inside ``graph.ingest_episode``: that function returns ``str | None``
    where None already means "extraction failed", and a skip is not a failure.

    This governs the GRAPH only. The page is still chunked, embedded and
    retrievable from Qdrant, which is where a link list belongs — someone
    searching for "overzicht" should still find it.
    """
    if not document_text:
        return None
    without_images = _MD_IMAGE.sub(" ", document_text)
    links = len(_MD_LINK.findall(without_images))
    if links < _NAV_MIN_LINKS:
        return None
    prose = _LIST_FURNITURE.sub(" ", _MD_LINK.sub(" ", without_images))
    prose_chars = len("".join(prose.split()))
    if prose_chars < links * _NAV_MAX_PROSE_PER_LINK:
        return "navigation_page"
    return None
