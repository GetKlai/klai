"""Lookalikes of another product (knowledge-base plan, step 7).

Collapsed before the reranker; the question's product is boosted after it.

Synthetic data only: fictional products on example.com.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.services.lookalike import demote_other_products

# Two b-bit MinHash fingerprints that agree on 54 of 64 positions (a lookalike)
# and one that agrees with neither.
FINGERPRINT = "ab" * 64
LOOKALIKE = "cd" * 10 + "ab" * 54
UNRELATED = "ef" * 64


def _chunk(name: str, **extra) -> dict:
    return {
        "chunk_id": name,
        "text": f"{name} text",
        "score": 0.5,
        "reranker_score": 0.5,
        "source_url": f"https://help.example.com/{name}",
        **extra,
    }


def _post(client, body: dict, *, searched: list[dict], reranked: list[dict] | None = None):
    """One /retrieve call with search mocked; the reranker returns its input unless told."""
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(
            patch(
                "retrieval_api.api.retrieve.coreference.resolve", AsyncMock(return_value="resolved")
            )
        )
        enter(patch("retrieval_api.api.retrieve.embed_single", AsyncMock(return_value=[1.0])))
        enter(patch("retrieval_api.api.retrieve.embed_sparse", AsyncMock(return_value=None)))
        enter(
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search", AsyncMock(return_value=searched)
            )
        )
        rerank = enter(
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                AsyncMock(side_effect=lambda query, candidates, top_n: reranked or candidates),
            )
        )
        enter(
            patch("retrieval_api.services.parent_lookup.fetch_parents", AsyncMock(return_value={}))
        )
        settings = enter(patch("retrieval_api.api.retrieve.settings"))
        settings.ranking_contract_mode = "active"
        settings.retrieval_candidates = 60
        settings.reranker_candidates = 60
        settings.reranker_enabled = True
        settings.graphiti_enabled = False
        settings.source_quota_enabled = False
        settings.router_enabled = False
        settings.retrieval_quality_floor = 0.05
        settings.confidence_band_high_threshold = 0.60
        settings.confidence_band_low_threshold = 0.30
        settings.lookalike_min_similarity = 0.7
        settings.product_boost = 0.05
        return client.post("/retrieve", json=body), rerank


SEARCHED = [
    _chunk("zephyr-forwarding", product="zephyr", text_fingerprint=FINGERPRINT, score=0.9),
    _chunk("invoices", product="", text_fingerprint=UNRELATED, score=0.8),
    _chunk("orbit-forwarding", product="Orbit", text_fingerprint=LOOKALIKE, score=0.7),
]


def test_a_lookalike_of_the_questions_product_is_kept_before_reranking(
    client, sample_retrieve_request
):
    _, rerank = _post(client, {**sample_retrieve_request, "product": "orbit"}, searched=SEARCHED)

    assert [c["chunk_id"] for c in rerank.await_args.args[1]] == ["orbit-forwarding", "invoices"]


def test_without_the_questions_product_the_higher_scoring_lookalike_is_kept(
    client, sample_retrieve_request
):
    _, rerank = _post(client, sample_retrieve_request, searched=SEARCHED)

    assert [c["chunk_id"] for c in rerank.await_args.args[1]] == ["zephyr-forwarding", "invoices"]


def test_a_later_candidate_is_compared_with_the_lookalike_that_was_kept(
    client, sample_retrieve_request
):
    # Like the dropped zephyr chunk (50 of 64 positions) but not like the kept
    # orbit chunk (40 of 64), so it stays a candidate of its own.
    near_the_dropped_one = "ab" * 50 + "ef" * 14
    searched = [*SEARCHED, _chunk("zephyr-voicemail", text_fingerprint=near_the_dropped_one)]

    _, rerank = _post(client, {**sample_retrieve_request, "product": "orbit"}, searched=searched)

    assert [c["chunk_id"] for c in rerank.await_args.args[1]] == [
        "orbit-forwarding",
        "invoices",
        "zephyr-voicemail",
    ]


def test_another_product_s_chunk_is_demoted_and_never_filtered(client, sample_retrieve_request):
    response, _ = _post(
        client,
        {**sample_retrieve_request, "product": "Orbit", "top_k": 5},
        searched=[_chunk("x")],
        reranked=[
            _chunk("other-product", product="zephyr", reranker_score=0.60),
            _chunk("orbit-close", product="orbit", reranker_score=0.58),
            _chunk("orbit-far", product="orbit", reranker_score=0.30),
            _chunk("unlabelled", reranker_score=0.40),
        ],
    )

    chunks = response.json()["chunks"]
    assert [c["chunk_id"] for c in chunks] == [
        "orbit-close",
        "other-product",
        "unlabelled",
        "orbit-far",
    ]
    assert chunks[0]["score"] == 0.58
    assert chunks[1]["score"] == pytest.approx(0.60 - 0.05)
    assert chunks[1]["reranker_score"] == 0.60
    assert chunks[1]["product"] == "zephyr"


def _ranked(*chunks: tuple[str, str, float]) -> list[dict]:
    return [_chunk(name, product=product, reranker_score=score) for name, product, score in chunks]


def test_without_another_product_among_the_candidates_nothing_moves():
    """One labelled help centre beside an unlabelled wiki: a boost lifted every
    labelled chunk over the wiki's answers (plan §6, 24 October 2026)."""
    chunks = _ranked(("wiki-1", "", 0.60), ("orbit-1", "orbit", 0.58), ("wiki-2", "", 0.55))
    before = [(c["chunk_id"], c["reranker_score"]) for c in chunks]

    assert demote_other_products(chunks, "Orbit", 0.05) == (0, "no_other_product")
    assert [(c["chunk_id"], c["reranker_score"]) for c in chunks] == before


def test_only_another_product_s_chunks_sink_and_unlabelled_ones_keep_their_place():
    chunks = _ranked(
        ("wiki-1", "", 0.60),
        ("orbit-1", "orbit", 0.58),
        ("zephyr-1", "zephyr", 0.57),
        ("wiki-2", "", 0.55),
    )

    assert demote_other_products(chunks, "Orbit", 0.05) == (1, "applied")
    assert [(c["chunk_id"], c["reranker_score"]) for c in chunks] == [
        ("wiki-1", 0.60),
        ("orbit-1", 0.58),
        ("wiki-2", 0.55),
        ("zephyr-1", pytest.approx(0.57 - 0.05)),
    ]


def test_another_product_s_chunk_falls_below_the_question_s_only_by_the_arithmetic():
    chunks = _ranked(("zephyr-1", "zephyr", 0.60), ("orbit-1", "orbit", 0.58))

    demote_other_products(chunks, "Orbit", 0.05)

    order = [c["chunk_id"] for c in chunks]
    assert (order.index("zephyr-1") > order.index("orbit-1")) == (0.60 - 0.05 < 0.58)
