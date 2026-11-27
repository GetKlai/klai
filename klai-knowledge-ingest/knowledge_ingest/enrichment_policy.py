"""Shared policy for deciding when LLM enrichment should run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from knowledge_ingest.config import settings

_DEFAULT_BATCH_CHUNKS = 200
_DEFAULT_HARD_MAX_CHUNKS = 2000

# Version 1 is the implicit, never-stamped legacy ruleset from before #1148.
# Version 2 is the subject-facts, navigation-page skip, cross-language entity
# naming ruleset used for the 2026-08-22 rebuild of a support knowledge base. Bump this whenever the
# extraction prompt in graph.py or the skip rules below change semantically.
GRAPHITI_EXTRACTION_VERSION = 2


def resumable_episode_ids(extra: Mapping[str, Any]) -> list[str]:
    """Return the parts an unfinished run of the current extraction already wrote.

    A retried, zombie-recovered or re-run extraction would otherwise start at
    part 1 again, paying for every finished part twice and appending duplicate
    episodes. ``graphiti_episode_ids_version`` is written before the first part
    is extracted, so the ids only count when it matches: ids from an older
    extraction version are exactly what a replacement run must delete.

    ``graphiti_episode_ids_from`` is where this extraction's parts start: a
    re-extraction of the same row keeps the previous extraction's ids in front
    of its own until it completes, so they stay recorded and searchable.
    """
    if extra.get("graphiti_episode_complete") is not False:
        return []
    if extra.get("graphiti_episode_ids_version") != GRAPHITI_EXTRACTION_VERSION:
        return []
    return list(extra.get("graphiti_episode_ids") or [])[
        int(extra.get("graphiti_episode_ids_from") or 0) :
    ]


def enrichment_batch_size() -> int:
    """Chunks one enrichment task handles; a larger document takes several tasks."""
    value = getattr(settings, "enrichment_max_chunks", _DEFAULT_BATCH_CHUNKS)
    return value if isinstance(value, int) and value > 0 else _DEFAULT_BATCH_CHUNKS


def _hard_max_chunks() -> int:
    value = getattr(settings, "enrichment_hard_max_chunks", _DEFAULT_HARD_MAX_CHUNKS)
    return value if isinstance(value, int) else _DEFAULT_HARD_MAX_CHUNKS


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
# largest — and worst — index pages through. Measured on a support knowledge
# base's crawled corpus (2026-08-22):
#
#   example.com/nl/phone-software    90 links     252 prose      2.8 per link
#   example.com/help/                    34 links     130 prose      3.8 per link
#   example.com/nl/crm-software     211 links     972 prose      4.6 per link
#   example.com/terms/                  125 links    1231 prose      9.8 per link
#   example.com/help/integrations        12 links     323 prose     26.9 per link
#   example.com/help/getting-started      6 links    3573 prose    595.5 per link
#   example.com/help/handset-features    32 links   19654 prose    614.2 per link
#
# Index pages sit under 10, real articles over 500 — two orders of magnitude
# apart. 40 sits in the empty middle with a 15x margin to the nearest article,
# which is the margin worth having: a false positive silently removes a real
# document from the graph, and nobody would notice.
#
# Neither link count nor sentence count survives on its own. A handset-features
# article carries as many links as the index page, so counting links throws
# out one of the richest documents in the corpus. Counting sentence
# punctuation breaks the other way: a list written "* [Article](url)." scores
# one sentence per link and slips through, while a language whose sentences do
# not end in ASCII "." scores zero and is discarded. The question is "does this
# page contain writing", so measure the writing.
_NAV_MAX_PROSE_PER_LINK = 40
#
# Listing pages with excerpts broke that measure. A crawled search-result list
# is a numbered list of results, each a heading holding one link followed by one
# line of excerpt; it scored ~170 characters of prose per link and passed as an
# article, so ~950 such pages of one help site were rewritten on most crawls at
# the cost of enrichment and graph extraction. The excerpt is the page pointing
# at another page, so the text after a link-only line is set aside as a teaser
# when it is at most this long, and a longer run counts as writing in full.
# Measured on fit markdown (2026-11-27), per link before -> after teasers:
#
#   search-result list, 20 results     23 links   3907 prose   169.9 ->  26.0
#   category archive with excerpts    102 links  26482 prose   259.6 ->   0.0
#   blog index with teasers            31 links   3958 prose   127.7 ->  37.3
#   encyclopedia article              418 links  23092 prose    55.2 ->  55.3
#   help article, many links           50 links   4940 prose    98.8 ->  86.1
#   guide                              22 links   2387 prose   108.5 ->  89.1
#   how-to                             16 links   4369 prose   273.1 -> 273.1
#   reference article                  62 links  27106 prose   437.2 -> 436.3
#
# The run after a link-only line is median 164 / p90 175 on the search list,
# 284 / 315 on the category archive and 113 / 264 on the blog index, against a
# median of 299 to 651 in articles. 400 sits above every listing p90. The margin
# is thin on one side: the blog index lands at 37.3 against the limit of 40,
# the nearest article at 55.3. Not caught: listings whose items are not
# link-only lines, such as card layouts.
_NAV_MAX_TEASER_CHARS = 400
# A line that is one link to another page once list and heading furniture, a
# list number and emphasis marks are stripped. A target with a fragment is a
# heading's own anchor (``## [Section](#section)``), which documentation sites
# put on every heading: the short section under it is writing, not a teaser.
# Stripped with str.strip, not matched by one pattern: the furniture and
# emphasis sets overlap, and a pattern with both took 0.87 s on a line of
# 20,000 dashes.
_LINK_ONLY = re.compile(r"\[[^\]]*\]\([^)#]*\)")
_LINE_FURNITURE = " \t>*-+#|_"
_LIST_NUMBER = re.compile(r"\d+[.)]")


def _is_link_only_line(line: str) -> bool:
    core = _LIST_NUMBER.sub("", line.strip(_LINE_FURNITURE), count=1).strip(_LINE_FURNITURE)
    return _LINK_ONLY.fullmatch(core) is not None


def _navigation_prose_chars(text_without_images: str) -> int:
    """Characters of writing on the page, with teasers after link-only lines set aside."""
    total = 0
    run = 0
    after_link_only = False

    def close_run() -> int:
        return run if run > _NAV_MAX_TEASER_CHARS or not after_link_only else 0

    for line in text_without_images.splitlines():
        prose = len("".join(_LIST_FURNITURE.sub(" ", _MD_LINK.sub(" ", line)).split()))
        if _is_link_only_line(line):
            total += close_run() + prose
            run = 0
            after_link_only = True
        else:
            run += prose
    return total + close_run()


def enrichment_skip_reason(
    *,
    chunk_count: int,
    extra_payload: Mapping[str, Any] | None,
    document_text: str | None = None,
) -> str | None:
    """Return a machine-readable reason when LLM enrichment should not run at all.

    Size and truncation only bound the work: a document above
    ``enrichment_batch_size`` is enriched in batches (see
    ``enrichment_tasks._load_and_enrich``), so every chunk eventually carries
    its context sentence and questions. Only a document above the hard ceiling
    is refused, and it keeps its raw chunks, which already carry the dense and
    the sparse vector. ``docling_chunk_count`` counts the chunks of a truncated
    upload, whose stored text is only a preview.

    A navigation page (``graph_episode_skip_reason``) is refused too, when the
    caller passes the text: it exists to point at other pages, so enrichment
    would pay LLM calls for context sentences about a list of links. It keeps
    its raw chunks, as a ``too_many_chunks`` document does.
    """
    extra = extra_payload or {}
    try:
        docling_chunk_count = int(extra.get("docling_chunk_count") or 0)
    except (TypeError, ValueError):
        docling_chunk_count = 0
    hard_max = _hard_max_chunks()
    if hard_max > 0 and max(chunk_count, docling_chunk_count) > hard_max:
        return "too_many_chunks"
    return graph_episode_skip_reason(document_text)


def graph_episode_skip_reason(document_text: str | None) -> str | None:
    """Return a reason when a document should NOT become a graph episode.

    A knowledge base contains pages that exist to point at other pages: index
    pages, category listings, tables of contents. They hold no facts about the
    world, only facts about the documentation, so extraction can only produce
    the meta-statements ``_EXTRACTION_INSTRUCTIONS`` rule 1 forbids
    (GetKlai/klai#1148). Replaying a help-centre index page through extraction
    on 2026-08-22 returned nothing but statements of the form "One of the
    documentation articles for <product> is titled <title>" and eleven siblings.

    Skipping them is cheaper than teaching a prompt to reject them one edge at
    a time, and it is not free to get wrong in either direction: each episode
    costs roughly 26 LLM calls out of the shared klai-ingest budget, and a false
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
    if _navigation_prose_chars(without_images) < links * _NAV_MAX_PROSE_PER_LINK:
        return "navigation_page"
    return None
