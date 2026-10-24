"""Lookalike articles of another product (knowledge-base plan, step 7).

Help centres publish the same article per product with another product name,
and in the September baseline a sixth of the wrongly judged chat turns
answered from such a lookalike. knowledge-ingest stores per chunk the product
it is about (``product``) and a b-bit MinHash of its text
(``text_fingerprint``, 64 positions of one byte, as hex; see
knowledge_ingest/utils/content_fingerprint.py). Before the reranker, near-
identical candidates collapse into one, so lookalikes no longer fill the slots
side by side; after it, the chunks labelled with another product sink by a
small amount. Unlabelled chunks and the question's own keep their scores and
their order among each other. Nothing is ever dropped for its product alone.
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


def demote_other_products(chunks: list[dict], product: str, amount: float) -> tuple[int, str]:
    """Lower the chunks labelled with a product other than ``product``; how many and why.

    A demotion, not a boost of the question's product: a boost lifted every
    labelled chunk over the unlabelled ones, and with one labelled help centre
    beside an unlabelled wiki that cost the meetlat up to three served cases
    of 22 on 24 October 2026. An unlabelled chunk is no other product.

    The ranking truth (``final_rank_score``) when the ranking contract is
    active, else the reranker score, as the page-context boost does. With the
    contract active the reranker score itself stays, but the confidence band
    follows the ranking score, so when the best evidence is another product's
    the band can fall; that is accepted, since the band then describes
    evidence for the wrong product.
    """
    demoted = 0
    for chunk in chunks:
        if not chunk.get("product") or _same_product(chunk, product):
            continue
        key = next(
            key
            for key in ("final_rank_score", "reranker_score", "score")
            if isinstance(chunk.get(key), (int, float))
        )
        chunk[key] -= amount
        demoted += 1
    if not demoted:
        return 0, "no_other_product"
    chunks.sort(key=lambda c: ranking_score(c, "reranker_score", "score"), reverse=True)
    return demoted, "applied"
