"""debug_stages: the sources each stage saw, for the recall measurement (meetlat retrieval)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.config import Settings


def _chunk(name: str, **extra) -> dict:
    return {
        "chunk_id": name,
        "text": f"{name} text",
        "score": 0.5,
        "reranker_score": 0.5,
        "source_url": f"https://example.com/{name}/",
        **extra,
    }


def _post(
    client,
    body: dict,
    *,
    searched: list[dict] | Callable,
    reranked: list[dict],
    reranker_candidates: int = 20,
):
    """One /retrieve call with search and rerank mocked; the response and the rerank mock."""
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(
            patch(
                "retrieval_api.api.retrieve.coreference.resolve", AsyncMock(return_value="resolved")
            )
        )
        enter(
            patch(
                "retrieval_api.api.retrieve.embed_single",
                AsyncMock(side_effect=lambda text: [float(len(text))]),
            )
        )
        enter(patch("retrieval_api.api.retrieve.embed_sparse", AsyncMock(return_value=None)))
        search = (
            AsyncMock(side_effect=searched)
            if callable(searched)
            else AsyncMock(return_value=searched)
        )
        enter(patch("retrieval_api.api.retrieve.search.hybrid_search", search))
        rerank = enter(
            patch("retrieval_api.api.retrieve.reranker.rerank", AsyncMock(return_value=reranked))
        )
        enter(
            patch("retrieval_api.services.parent_lookup.fetch_parents", AsyncMock(return_value={}))
        )
        settings = enter(patch("retrieval_api.api.retrieve.settings"))
        settings.ranking_contract_mode = "active"
        settings.retrieval_candidates = 60
        settings.reranker_candidates = reranker_candidates
        settings.sub_query_top_k = 4
        settings.sub_query_max_concurrent = 3
        settings.reranker_enabled = True
        settings.graphiti_enabled = False
        settings.source_quota_enabled = False
        settings.router_enabled = False
        settings.retrieval_quality_floor = 0.05
        settings.confidence_band_high_threshold = 0.60
        settings.confidence_band_low_threshold = 0.30
        return client.post("/retrieve", json=body), rerank


SEARCHED = [_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d")]
RERANKED = [_chunk("c", reranker_score=0.9), _chunk("a", reranker_score=0.4)]


def test_debug_stages_are_absent_unless_requested(client, sample_retrieve_request):
    response, _ = _post(client, sample_retrieve_request, searched=SEARCHED, reranked=RERANKED)

    assert response.status_code == 200
    assert "debug_stages" not in response.json()


def test_debug_stages_list_the_source_keys_each_stage_saw(client, sample_retrieve_request):
    response, _ = _post(
        client,
        {**sample_retrieve_request, "debug_stages": True},
        searched=SEARCHED,
        reranked=RERANKED,
    )

    stages = response.json()["debug_stages"]
    # The key the evidence pack assigns source slots with: the normalised URL, no trailing slash.
    assert stages["search"] == [f"https://example.com/{name}" for name in "abcd"]
    assert stages["rerank"] == ["https://example.com/c", "https://example.com/a"]


def test_debug_stages_carry_no_chunk_text(client, sample_retrieve_request):
    response, _ = _post(
        client,
        {**sample_retrieve_request, "debug_stages": True},
        searched=SEARCHED,
        reranked=RERANKED,
    )

    assert "a text" not in str(response.json()["debug_stages"])


def test_reranker_sees_as_many_candidates_as_the_setting_says(client, sample_retrieve_request):
    _, rerank = _post(
        client, sample_retrieve_request, searched=SEARCHED, reranked=RERANKED, reranker_candidates=3
    )

    assert [c["chunk_id"] for c in rerank.await_args.args[1]] == ["a", "b", "c"]


def test_reranker_candidates_default_to_sixty_and_follow_the_environment(monkeypatch):
    monkeypatch.delenv("RERANKER_CANDIDATES", raising=False)
    assert Settings().reranker_candidates == 60

    monkeypatch.setenv("RERANKER_CANDIDATES", "20")
    assert Settings().reranker_candidates == 20


@pytest.mark.parametrize("flag", [True, False])
def test_sub_question_fan_out_merges_the_stages_of_every_question(
    client, sample_retrieve_request, flag
):
    body = {**sample_retrieve_request, "sub_queries": ["first?", "second?"], "debug_stages": flag}
    response, _ = _post(client, body, searched=SEARCHED, reranked=RERANKED)

    stages = response.json().get("debug_stages")
    if flag:
        # Both questions saw the same four sources; the merged stage lists each once.
        assert stages["search"] == [f"https://example.com/{name}" for name in "abcd"]
    else:
        assert stages is None


def test_debug_stages_include_what_a_query_variant_pass_saw(client, sample_retrieve_request):
    def searched(query_vector, *_args, **_kwargs):
        # The variant's text is longer than "resolved", so its vector tells the two passes apart.
        return [_chunk("variant")] if query_vector[0] > len("resolved") else SEARCHED

    body = {
        **sample_retrieve_request,
        "query_variants": ["another formulation"],
        "debug_stages": True,
    }
    response, _ = _post(client, body, searched=searched, reranked=RERANKED)

    assert "https://example.com/variant" in response.json()["debug_stages"]["search"]
