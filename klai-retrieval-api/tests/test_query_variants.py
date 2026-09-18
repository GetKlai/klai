"""Caller-supplied variants of the query are extra retrieval passes, fused after reranking.

SPEC-RAG-ANSWER-JUDGES-001, logbook 2.27 and 2.33. A visitor's own words often
miss the article that answers them; two paraphrases run as their own passes
and RRF-fused with the main pass raised the share of first questions with an
answering passage in the top-8 from 35% to 59%, and won the blind end-to-end
comparison 63 against 43. The same words as pre-rerank prefetch legs changed
nothing: the reranker, scoring against the bare query, pushed them back out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

_CHUNK = {
    "score": 0.5,
    "artifact_id": None,
    "content_type": None,
    "context_prefix": None,
    "scope": "org",
    "title": "t",
    "source_url": None,
    "source_label": "support",
    "valid_at": None,
    "invalid_at": None,
    "ingested_at": None,
    "assertion_mode": None,
}

_VARIANTS = ["wachtrij toevoegen aan belplan", "belplan wachtrij instellen"]


def _chunk(chunk_id: str, text: str) -> dict:
    return {**_CHUNK, "chunk_id": chunk_id, "text": text}


async def _identity_rerank(_query: str, candidates: list[dict], top_k: int) -> list[dict]:
    return [{**c, "reranker_score": 0.9} for c in candidates[:top_k]]


def _patches(search_side_effect, embed_calls: list[str]):
    async def _embed(text: str) -> list[float]:
        embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    return (
        patch(
            "retrieval_api.api.retrieve.coreference.resolve",
            new_callable=AsyncMock,
            return_value="hoe stel ik een wachtrij in mijn belplan in",
        ),
        patch("retrieval_api.api.retrieve.embed_single", side_effect=_embed),
        patch("retrieval_api.api.retrieve.embed_sparse", new_callable=AsyncMock, return_value=None),
        patch("retrieval_api.api.retrieve.search.hybrid_search", side_effect=search_side_effect),
        patch(
            "retrieval_api.api.retrieve.graph_search.search",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "retrieval_api.api.retrieve.fetch_source_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("retrieval_api.api.retrieve.reranker.rerank", side_effect=_identity_rerank),
    )


def _post(client, variants: list[str] | None):
    body = {"query": "hoe stel ik een wachtrij in?", "org_id": "org-1", "scope": "org", "top_k": 8}
    if variants is not None:
        body["query_variants"] = variants
    return client.post("/retrieve", json=body)


def test_variants_fuse_what_only_they_retrieve(client):
    """A chunk only a variant pass finds reaches the caller, and each variant is
    embedded on its own words."""
    embed_calls: list[str] = []
    calls = {"n": 0}

    async def _search(query_vector, req, candidates, sparse_vector=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_chunk("main", "belplan")]
        return [_chunk(f"variant-{calls['n']}", "wachtrij")]

    with _patch_all(_patches(_search, embed_calls)):
        resp = _post(client, _VARIANTS)

    assert resp.status_code == 200
    ids = {chunk["chunk_id"] for chunk in resp.json()["chunks"]}
    assert ids == {"main", "variant-2", "variant-3"}
    assert all(variant in embed_calls for variant in _VARIANTS), embed_calls


def test_without_variants_one_search_runs(client):
    """No variants, and a variant equal to the query, add no pass."""
    calls = {"n": 0}

    async def _search(query_vector, req, candidates, sparse_vector=None, **_kwargs):
        calls["n"] += 1
        return [_chunk("main", "belplan")]

    with _patch_all(_patches(_search, [])):
        assert _post(client, None).status_code == 200
        assert _post(client, ["Hoe stel ik een wachtrij in?", " "]).status_code == 200

    assert calls["n"] == 2


def test_a_failing_variant_pass_leaves_the_main_result_alone(client):
    calls = {"n": 0}

    async def _search(query_vector, req, candidates, sparse_vector=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("qdrant down for the variant passes")
        return [_chunk("main", "belplan")]

    with _patch_all(_patches(_search, [])):
        resp = _post(client, _VARIANTS)

    assert resp.status_code == 200
    assert [chunk["chunk_id"] for chunk in resp.json()["chunks"]] == ["main"]


class _patch_all:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False
