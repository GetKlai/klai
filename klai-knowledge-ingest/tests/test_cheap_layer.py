"""The cheap layer of a chunk and the patch that recomputes it (kb_patch).

Synthetic data only: invented products on example.com.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest import cheap_layer, contextual, enrichment
from knowledge_ingest.enrichment import EnrichedChunk
from knowledge_ingest.scripts import kb_patch
from knowledge_ingest.utils.content_fingerprint import compute_minhash

ORG, KB = "org-test", "kb-test"
GROUPS = [["Orbit Desk", "Orbit app", "softphone"]]
RULES = [{"prefix": "help.example.com/orbit/", "product": "Orbit"}]
NAMING = "Install Orbit Desk from the download page and sign in with your work account."
OTHER = "Faxes arrive as a PDF in your mailbox within a minute of being sent."
ALIAS = "Also known as: Orbit app | softphone"
SOURCE = {"source_type": "crawl", "source_url": "https://help.example.com/orbit/install"}


def _enriched(text: str, **extra) -> dict:
    return {
        **SOURCE,
        "text": text,
        "context_prefix": "Context.",
        "text_enriched": f"Context.\n\n{text}",
        **extra,
    }


def test_a_chunk_naming_an_alias_gets_the_other_names_and_another_chunk_none():
    naming = cheap_layer.cheap_fields(_enriched(NAMING), GROUPS, [])
    other = cheap_layer.cheap_fields(_enriched(OTHER), GROUPS, [])

    assert naming["context_prefix"] == f"Context.\n{ALIAS}"
    assert naming["text_enriched"] == f"Context.\n\n{NAMING}\n\n{ALIAS}"
    assert (other["context_prefix"], other["text_enriched"]) == ("Context.", f"Context.\n\n{OTHER}")


def test_removing_an_alias_group_removes_its_line_again():
    stored = {**_enriched(NAMING), **cheap_layer.cheap_fields(_enriched(NAMING), GROUPS, [])}

    after = cheap_layer.cheap_fields(stored, [], [])

    assert (after["context_prefix"], after["text_enriched"]) == (
        "Context.",
        f"Context.\n\n{NAMING}",
    )


def test_anchor_vocabulary_is_not_taken_for_an_alias_line():
    """Ingest appends anchor texts to the embedded text with the same "Also known
    as:" label, but never to the context prefix; the patch must leave it."""
    anchored = _enriched(OTHER, text_enriched=f"Context.\n\n{OTHER}\n\nAlso known as: fax inbox")

    after = cheap_layer.cheap_fields(anchored, [], [])

    assert after["text_enriched"] == anchored["text_enriched"]


def test_the_product_follows_the_first_matching_rule_and_is_absent_without_one():
    rules = [*RULES, {"prefix": "help.example.com/", "product": "Generic"}]

    assert cheap_layer.cheap_fields(SOURCE, [], rules)["product"] == "Orbit"
    assert "product" not in cheap_layer.cheap_fields(SOURCE, [], [])


# --- kb_patch over an in-memory collection -------------------------------------------------


@pytest.fixture
async def mem_client():
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        qs.COLLECTION,
        vectors_config={
            "vector_chunk": VectorParams(size=2, distance=Distance.COSINE),
            "vector_questions": VectorParams(size=2, distance=Distance.COSINE),
        },
        sparse_vectors_config={"vector_sparse": SparseVectorParams()},
    )
    with patch.object(qs, "get_client", return_value=client):
        yield client
    await client.close()


def _dense(texts: list[str]) -> list[list[float]]:
    return [[float(len(t)), 1.0] for t in texts]


def _sparse(texts: list[str]) -> list[SparseVector]:
    return [SparseVector(indices=[len(t)], values=[1.0]) for t in texts]


def _unit(vector: list[float]) -> list[float]:
    norm = sum(x * x for x in vector) ** 0.5
    return [x / norm for x in vector]


@pytest.fixture
def embedders():
    dense, sparse = AsyncMock(side_effect=_dense), AsyncMock(side_effect=_sparse)
    with (
        patch.object(kb_patch.embedder, "embed", dense),
        patch.object(kb_patch.sparse_embedder, "embed_sparse_batch", sparse),
    ):
        yield dense, sparse


def _settings(groups: list, rules: list):
    return patch.object(
        kb_patch.kb_config, "org_kb_settings", AsyncMock(return_value={KB: (groups, rules)})
    )


def _point(n: int, text: str, **extra) -> PointStruct:
    payload = {"org_id": ORG, "kb_slug": KB, "path": f"orbit/{n}", **_enriched(text), **extra}
    vector = {"vector_chunk": _dense([payload["text_enriched"]])[0]}
    return PointStruct(id=n, vector=vector, payload=payload)


async def _stored(client) -> dict:
    points, _ = await client.scroll(qs.COLLECTION, limit=100, with_payload=True, with_vectors=True)
    return {p.id: p for p in points}


async def test_dry_run_counts_and_shows_the_first_ten_changes_and_writes_nothing(
    mem_client, embedders
):
    await mem_client.upsert(
        qs.COLLECTION,
        points=[_point(n, NAMING) for n in range(1, 13)] + [_point(13, NAMING, org_id="org-other")],
    )
    before = await _stored(mem_client)

    with _settings(GROUPS, RULES):
        report = await kb_patch.patch_org(mem_client, ORG, execute=False)

    result = report[KB]
    assert result.selected == 12
    assert result.changes["text_enriched"] == 12
    assert result.changes["product"] == 12
    assert len(result.examples) == 10
    assert result.written == 0
    assert await _stored(mem_client) == before
    embedders[0].assert_not_awaited()


async def test_an_unchanged_setting_writes_nothing(mem_client, embedders):
    await mem_client.upsert(qs.COLLECTION, points=[_point(1, NAMING), _point(2, OTHER)])
    with _settings(GROUPS, RULES):
        first = await kb_patch.patch_org(mem_client, ORG, execute=True)
        assert first[KB].written == 2
        with patch.object(
            mem_client, "batch_update_points", AsyncMock(side_effect=AssertionError("wrote"))
        ):
            again = await kb_patch.patch_org(mem_client, ORG, execute=True)

    assert (again[KB].selected, again[KB].written) == (0, 0)


async def test_a_changed_setting_reembeds_only_the_points_whose_text_changed(mem_client, embedders):
    await mem_client.upsert(
        qs.COLLECTION,
        points=[_point(1, NAMING), _point(2, OTHER), _point(3, NAMING, org_id="org-other")],
    )
    dense, sparse = embedders
    with _settings([], RULES):
        await kb_patch.patch_org(mem_client, ORG, execute=True)
    dense.assert_not_awaited()

    with _settings(GROUPS, RULES):
        report = await kb_patch.patch_org(mem_client, ORG, execute=True)

    new_text = f"Context.\n\n{NAMING}\n\n{ALIAS}"
    dense.assert_awaited_once_with([new_text])
    sparse.assert_awaited_once_with([new_text])
    assert (report[KB].written, report[KB].reembedded) == (2, 1)
    after = await _stored(mem_client)
    assert after[1].payload["text_enriched"] == new_text
    # Qdrant stores cosine vectors normalised.
    assert after[1].vector["vector_chunk"] == pytest.approx(_unit(_dense([new_text])[0]))
    assert after[1].vector["vector_sparse"].indices == [len(new_text)]
    assert after[2].payload["text_enriched"] == f"Context.\n\n{OTHER}"
    assert "product" not in after[3].payload
    assert after[3].payload["text_enriched"] == f"Context.\n\n{NAMING}"


async def test_the_patch_makes_no_llm_call(mem_client, embedders):
    await mem_client.upsert(
        qs.COLLECTION,
        points=[_point(1, NAMING), _point(2, OTHER, text_fingerprint="", product="Old")],
    )
    llm = AsyncMock(side_effect=AssertionError("LLM called"))
    no_http = AsyncMock(side_effect=AssertionError("HTTP call to LiteLLM"))
    with (
        _settings(GROUPS, RULES),
        patch.object(enrichment, "_call_llm", llm),
        patch.object(contextual, "generate_document_summary", llm),
        patch.object(httpx.AsyncClient, "send", no_http),
    ):
        report = await kb_patch.patch_org(mem_client, ORG, execute=True)

    assert report[KB].written == 2
    llm.assert_not_awaited()
    no_http.assert_not_awaited()


async def test_the_patch_stops_before_writing_when_the_settings_cannot_be_read(
    mem_client, embedders
):
    await mem_client.upsert(qs.COLLECTION, points=[_point(1, NAMING)])
    before = await _stored(mem_client)
    failing = AsyncMock(side_effect=OSError("database unreachable"))

    with patch.object(kb_patch.kb_config, "org_kb_settings", failing), pytest.raises(OSError):
        await kb_patch.patch_org(mem_client, ORG, execute=True)

    assert await _stored(mem_client) == before


async def test_ingest_and_patch_write_the_same_cheap_layer(mem_client, embedders):
    """A new document and a patched old one cannot drift: both go through cheap_fields."""

    async def ingest(path: str, groups: list, rules: list) -> None:
        chunk = EnrichedChunk(
            original_text=NAMING,
            enriched_text=f"Context.\n\n{NAMING}",
            context_prefix="Context.",
            questions=[],
        )
        extra = dict(SOURCE)
        cheap_layer.apply_to_enriched_chunks([chunk], extra, path, groups, rules)
        await qs.upsert_enriched_chunks(
            org_id=ORG,
            kb_slug=KB,
            path=path,
            enriched_chunks=[chunk],
            chunk_vectors=_dense([chunk.enriched_text]),
            question_vectors=[None],
            sparse_vectors=_sparse([chunk.enriched_text]),
            artifact_id=path,
            extra_payload=extra,
        )

    await ingest("orbit/old", [], [])
    with _settings(GROUPS, RULES):
        await kb_patch.patch_org(mem_client, ORG, execute=True)
    await ingest("orbit/new", GROUPS, RULES)

    by_path = {p.payload["path"]: p for p in (await _stored(mem_client)).values()}
    old, new = by_path["orbit/old"], by_path["orbit/new"]
    for key in ("context_prefix", "text_enriched", "product", "text_fingerprint"):
        assert old.payload[key] == new.payload[key], key
    assert old.payload["cheap_layer_version"] == new.payload["cheap_layer_version"]
    assert old.vector["vector_chunk"] == pytest.approx(new.vector["vector_chunk"])
    assert old.vector["vector_sparse"] == new.vector["vector_sparse"]
    assert new.payload["text_fingerprint"] == compute_minhash(NAMING)
