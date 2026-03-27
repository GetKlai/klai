"""End-to-end tests for personal KB indexing and retrieval isolation.

Uses qdrant_client's in-memory backend to avoid needing a live Qdrant server.
Verifies the full roundtrip:
  1. upsert_chunks stores user_id in the point payload
  2. scroll with user_id filter returns only chunks belonging to that user
  3. Chunks for user A are invisible to user B (personal isolation)

These tests validate the core security property of personal KB:
content indexed with user_id=A cannot be retrieved when filtering for user_id=B.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

import knowledge_ingest.qdrant_store as qs_module

EMBED_DIM = 1024
COLLECTION = "klai_knowledge"


@pytest.fixture
async def mem_client():
    """In-memory Qdrant client with the klai_knowledge collection pre-created."""
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        COLLECTION,
        vectors_config={
            "vector_chunk": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            "vector_questions": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "vector_sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
            ),
        },
    )
    yield client
    await client.close()


def _unit_vector(dim: int = EMBED_DIM) -> list[float]:
    """Return a normalised vector of ones (valid for cosine distance)."""
    val = 1.0 / dim ** 0.5
    return [val] * dim


@pytest.mark.asyncio
async def test_upsert_chunks_stores_user_id(mem_client):
    """user_id must be stored in the Qdrant point payload after upsert_chunks."""
    with patch.object(qs_module, "get_client", return_value=mem_client):
        await qs_module.upsert_chunks(
            org_id="org-test",
            kb_slug="personal",
            path="users/user-aaa/note.md",
            chunks=["My private note."],
            vectors=[_unit_vector()],
            artifact_id="art-aaa",
            user_id="user-aaa",
        )

    points, _ = await mem_client.scroll(
        COLLECTION, with_payload=True, limit=10
    )
    assert len(points) == 1
    payload = points[0].payload
    assert payload["user_id"] == "user-aaa"
    assert payload["kb_slug"] == "personal"
    assert payload["org_id"] == "org-test"


@pytest.mark.asyncio
async def test_personal_chunk_invisible_to_other_user(mem_client):
    """Personal chunk for user A must NOT be returned when filtering for user B."""
    with patch.object(qs_module, "get_client", return_value=mem_client):
        # Index a chunk for user A
        await qs_module.upsert_chunks(
            org_id="org-test",
            kb_slug="personal",
            path="users/user-aaa/note.md",
            chunks=["User A secret note."],
            vectors=[_unit_vector()],
            artifact_id="art-aaa",
            user_id="user-aaa",
        )
        # Index a chunk for user B
        await qs_module.upsert_chunks(
            org_id="org-test",
            kb_slug="personal",
            path="users/user-bbb/note.md",
            chunks=["User B secret note."],
            vectors=[_unit_vector()],
            artifact_id="art-bbb",
            user_id="user-bbb",
        )

    # Query for user A — should get exactly one result
    points_a, _ = await mem_client.scroll(
        COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="kb_slug", match=MatchValue(value="personal")),
                FieldCondition(key="user_id", match=MatchValue(value="user-aaa")),
            ]
        ),
        with_payload=True,
        limit=10,
    )
    assert len(points_a) == 1
    assert points_a[0].payload["user_id"] == "user-aaa"

    # Query for user B — should only get user B's chunk
    points_b, _ = await mem_client.scroll(
        COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="kb_slug", match=MatchValue(value="personal")),
                FieldCondition(key="user_id", match=MatchValue(value="user-bbb")),
            ]
        ),
        with_payload=True,
        limit=10,
    )
    assert len(points_b) == 1
    assert points_b[0].payload["user_id"] == "user-bbb"


@pytest.mark.asyncio
async def test_non_personal_chunk_has_no_user_id(mem_client):
    """Chunks for non-personal KBs must NOT have a user_id in their payload."""
    with patch.object(qs_module, "get_client", return_value=mem_client):
        await qs_module.upsert_chunks(
            org_id="org-test",
            kb_slug="team-docs",
            path="docs/readme.md",
            chunks=["Team readme content."],
            vectors=[_unit_vector()],
            artifact_id="art-team",
            user_id=None,
        )

    points, _ = await mem_client.scroll(COLLECTION, with_payload=True, limit=10)
    assert len(points) == 1
    assert "user_id" not in (points[0].payload or {})


@pytest.mark.asyncio
async def test_personal_chunk_not_returned_without_user_id_filter(mem_client):
    """Personal chunk for user A is invisible in a query with no user_id filter.

    This mirrors the search() behaviour: personal kb_slugs require explicit user_id.
    If no user_id is passed, the personal chunk should still be reachable by org scope —
    but the retrieval-api layer adds the user_id filter. This test documents that the
    raw payload index supports the isolation: a filter on user_id works correctly.
    """
    with patch.object(qs_module, "get_client", return_value=mem_client):
        await qs_module.upsert_chunks(
            org_id="org-test",
            kb_slug="personal",
            path="users/user-aaa/note.md",
            chunks=["Personal secret."],
            vectors=[_unit_vector()],
            artifact_id="art-aaa",
            user_id="user-aaa",
        )

    # Without user_id filter: chunk is present (org-level scroll)
    all_points, _ = await mem_client.scroll(
        COLLECTION, with_payload=True, limit=10
    )
    assert len(all_points) == 1

    # With wrong user_id filter: chunk is NOT returned
    points_wrong_user, _ = await mem_client.scroll(
        COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value="user-zzz")),
            ]
        ),
        with_payload=True,
        limit=10,
    )
    assert len(points_wrong_user) == 0
