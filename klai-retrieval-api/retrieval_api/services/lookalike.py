"""Lookalike articles of another product (knowledge-base plan, step 7).

Help centres publish the same article per product with another product name,
and in the September baseline a sixth of the wrongly judged chat turns
answered from such a lookalike. knowledge-ingest stores per chunk the product
it is about (``product``) and a b-bit MinHash of its text
(``text_fingerprint``, 64 positions of one byte, as hex; see
knowledge_ingest/utils/content_fingerprint.py). Before the reranker, near-
identical candidates collapse into one, so lookalikes no longer fill the slots
side by side; after it, the chunks of the question's product get a small
additive bonus, but only when another product's chunk is among the candidates.
Nothing is ever dropped for its product alone.
"""

from __future__ import annotations

from operator import eq

from retrieval_api.util.scores import ranking_score

# The share of equal positions two random fingerprints have by chance: one in
# 256 per position.
_CHANCE = 1 / 256


def _same_product(chunk: dict, product: str) -> bool:
    return bool(product) and str(chunk.get("product") or "").casefold() == product.casefold()


def _similarity(a: bytes, b: bytes) -> float:
    """The estimated share of word shingles two texts have in common."""
    equal = sum(map(eq, a, b)) / len(a)
    return (equal - _CHANCE) / (1 - _CHANCE)


def collapse_lookalikes(
    candidates: list[dict], product: str, min_similarity: float
) -> tuple[list[dict], int]:
    """One candidate per group of near-identical texts, and how many went.

    The kept one is the first of the group in ``candidates``' order (the
    search's ranking), unless a later one is about the question's product and
    the earlier one is not: then the later one takes the earlier's place. A
    candidate without a fingerprint (ingested before step 7, or a graph fact)
    is never collapsed. Quadratic in the candidates, 60 to 80 per pass.
    """
    kept: list[dict] = []
    fingerprints: list[bytes | None] = []
    for chunk in candidates:
        raw = chunk.get("text_fingerprint")
        fingerprint = bytes.fromhex(raw) if raw else None
        twin = next(
            (
                index
                for index, other in enumerate(fingerprints)
                if fingerprint
                and other
                and len(other) == len(fingerprint)
                and _similarity(fingerprint, other) >= min_similarity
            ),
            None,
        )
        if twin is None:
            kept.append(chunk)
            fingerprints.append(fingerprint)
        elif _same_product(chunk, product) and not _same_product(kept[twin], product):
            kept[twin] = chunk
            fingerprints[twin] = fingerprint
    return kept, len(candidates) - len(kept)


def boost_product(chunks: list[dict], product: str, boost: float) -> tuple[int, str]:
    """Boost the chunks about ``product`` additively and re-sort; the number boosted and why.

    Only a chunk labelled with another product is an alternative; an
    unlabelled chunk is neutral. Without an alternative nothing is boosted
    ("no_alternative"): one labelled help centre beside an unlabelled wiki
    otherwise lifts every labelled chunk over the wiki's answers, which cost
    the meetlat up to three served cases of 22 on 24 October 2026.

    The ranking truth (``final_rank_score``) when the ranking contract is
    active, else the reranker score, as the page-context boost does; with the
    contract active the reranker score itself, and with it the confidence
    band, stays as the reranker gave it.
    """
    if not any(chunk.get("product") and not _same_product(chunk, product) for chunk in chunks):
        return 0, "no_alternative"
    boosted = 0
    for chunk in chunks:
        if not _same_product(chunk, product):
            continue
        key = next(
            key
            for key in ("final_rank_score", "reranker_score", "score")
            if isinstance(chunk.get(key), (int, float))
        )
        chunk[key] += boost
        boosted += 1
    if boosted:
        chunks.sort(key=lambda c: ranking_score(c, "reranker_score", "score"), reverse=True)
    return boosted, "applied"
