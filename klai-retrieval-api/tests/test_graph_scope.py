"""A graph fact is served only inside the scope the vector legs would search.

The graph is partitioned by organisation alone, so a fact carries no knowledge
base, visibility or owner of its own. What places it in a scope is the
document it was extracted from. These tests run ``/retrieve`` against an
in-memory Qdrant holding the documents, so the real ``_scope_filter`` decides
which of the graph's facts the request may receive.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from retrieval_api.api.retrieve import _label_graph_results
from retrieval_api.models import RetrieveRequest
from retrieval_api.services import search

ORG = "org-1"
USER_A = "user-a"
USER_B = "user-b"

# (kb_slug, path, artifact_id, visibility, owner user_id)
_DOCUMENTS = [
    ("handbook", "leave-policy", "art-handbook", "internal", None),
    ("handbook", "expenses", "art-legacy-handbook", "internal", None),
    ("finance", "budget", "art-finance", "internal", None),
    (f"personal-{USER_A}", "notes-a", "art-personal-a", "private", USER_A),
    (f"personal-{USER_B}", "notes-b", "art-personal-b", "private", USER_B),
    ("board", "minutes", "art-board", "private", USER_B),
]


def _fact(fact_id: str, kb_slug=None, path=None, artifact_id=None) -> dict:
    """A graph result as ``graph_search._convert_results`` shapes it."""
    return {
        "chunk_id": f"graph:{fact_id}",
        "text": f"fact {fact_id}",
        "score": 0.5,
        "artifact_id": artifact_id,
        "graph_kb_slug": kb_slug,
        "graph_path": path,
        "content_type": "graph_edge",
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
    }


def _facts() -> list[dict]:
    return [
        _fact("selected-kb", "handbook", "leave-policy"),
        _fact("legacy-resolves", artifact_id="art-legacy-handbook"),
        _fact("other-kb", "finance", "budget"),
        _fact("own-personal", f"personal-{USER_A}", "notes-a"),
        _fact("other-personal", f"personal-{USER_B}", "notes-b"),
        _fact("other-private-kb", "board", "minutes"),
        _fact("other-org", "handbook", "foreign-page"),
        _fact("pointer-unresolved", "handbook", "deleted-page"),
        _fact("legacy-unresolved", artifact_id="art-superseded"),
        _fact("no-pointer"),
    ]


async def _seeded_qdrant() -> AsyncQdrantClient:
    client = AsyncQdrantClient(location=":memory:")
    await client.create_collection(
        "klai_knowledge",
        vectors_config={"vector_chunk": VectorParams(size=2, distance=Distance.COSINE)},
    )
    points = [
        PointStruct(
            id=i,
            vector={"vector_chunk": [1.0, 0.0]},
            payload={
                "org_id": ORG,
                "kb_slug": kb_slug,
                "path": path,
                "artifact_id": artifact_id,
                "visibility": visibility,
                "user_id": owner,
                "title": f"{kb_slug}/{path}",
                "source_url": f"https://docs.example.com/{kb_slug}/{path}",
            },
        )
        for i, (kb_slug, path, artifact_id, visibility, owner) in enumerate(_DOCUMENTS, 1)
    ]
    # Same (kb_slug, path) in another organisation: never the caller's document.
    points.append(
        PointStruct(
            id=len(points) + 1,
            vector={"vector_chunk": [1.0, 0.0]},
            payload={
                "org_id": "org-2",
                "kb_slug": "handbook",
                "path": "foreign-page",
                "artifact_id": "art-foreign",
                "visibility": "internal",
            },
        )
    )
    await client.upsert("klai_knowledge", points=points)
    return client


_ORG_WIDE = {"selected-kb", "legacy-resolves", "other-kb"}

# Request scope -> the facts the response may carry. Every fact not listed is
# out of scope for that request or cannot be placed in any scope.
_MATRIX = [
    pytest.param(
        {"scope": "org", "user_id": USER_A},
        _ORG_WIDE | {"own-personal"},
        id="org-wide",
    ),
    pytest.param(
        {"scope": "org", "user_id": USER_A, "kb_slugs": ["handbook"]},
        {"selected-kb", "legacy-resolves"},
        id="one-kb",
    ),
    pytest.param(
        {"scope": "personal", "user_id": USER_A},
        {"own-personal"},
        id="personal-only",
    ),
    pytest.param(
        {"scope": "both", "user_id": USER_A, "kb_slugs": ["finance"]},
        {"other-kb", "own-personal"},
        id="org-kb-plus-personal",
    ),
    pytest.param(
        {"scope": "both", "user_id": USER_A, "include_owned_private_kbs": True},
        _ORG_WIDE | {"own-personal"},
        id="all-kbs-plus-personal",
    ),
    pytest.param(
        {"scope": "org"},
        _ORG_WIDE,
        id="no-user-sees-no-private",
    ),
    pytest.param(
        {"scope": "both", "user_id": USER_A, "effective_role": "personal"},
        {"own-personal"},
        id="personal-role",
    ),
]


def _post_retrieve(client, body: dict, graph_facts: list[dict], qdrant: AsyncQdrantClient):
    with (
        patch(
            "retrieval_api.api.retrieve.coreference.resolve",
            new_callable=AsyncMock,
            return_value="q",
        ),
        patch(
            "retrieval_api.api.retrieve.embed_single",
            new_callable=AsyncMock,
            return_value=[1.0, 0.0],
        ),
        patch(
            "retrieval_api.api.retrieve.embed_sparse",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "retrieval_api.api.retrieve.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "retrieval_api.api.retrieve.search.extra_query_legs",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "retrieval_api.api.retrieve.graph_search.search",
            new_callable=AsyncMock,
            return_value=graph_facts,
        ),
        patch.object(search, "_get_client", return_value=qdrant),
        patch("retrieval_api.api.retrieve.settings") as mock_settings,
    ):
        mock_settings.retrieval_candidates = 60
        mock_settings.reranker_enabled = False
        mock_settings.graphiti_enabled = True
        mock_settings.source_quota_enabled = False
        mock_settings.router_enabled = False
        mock_settings.retrieval_quality_floor = 0.05
        mock_settings.confidence_band_high_threshold = 0.60
        mock_settings.confidence_band_low_threshold = 0.30
        return client.post("/retrieve", json={"query": "q", "org_id": ORG, "top_k": 20, **body})


@pytest.mark.parametrize(("scope", "expected"), _MATRIX)
def test_retrieve_serves_only_graph_facts_whose_document_is_in_the_request_scope(
    client, scope, expected
):
    qdrant = asyncio.run(_seeded_qdrant())

    resp = _post_retrieve(client, scope, _facts(), qdrant)

    assert resp.status_code == 200
    served = {
        c["chunk_id"].removeprefix("graph:")
        for c in resp.json()["chunks"]
        if c["chunk_id"].startswith("graph:")
    }
    assert served == expected
    assert resp.json()["metadata"]["graph_results_count"] == len(expected)


@pytest.mark.asyncio
async def test_graph_facts_are_withheld_when_the_graph_could_not_name_their_documents():
    """The episode lookup timing out leaves every fact without a pointer."""
    req = RetrieveRequest(query="q", org_id=ORG, scope="org", user_id=USER_A)

    assert await _label_graph_results([_fact("a"), _fact("b")], req) == []


@pytest.mark.asyncio
async def test_graph_facts_are_withheld_when_their_documents_cannot_be_looked_up():
    """A failed scope lookup places no fact in scope, so none is served."""
    failing = AsyncMock()
    failing.scroll.side_effect = RuntimeError("qdrant unavailable")
    req = RetrieveRequest(query="q", org_id=ORG, scope="org", user_id=USER_A)

    with patch.object(search, "_get_client", return_value=failing):
        kept = await _label_graph_results(_facts(), req)

    assert kept == []
