"""The cheap layer of a chunk and the patch that recomputes it (kb_patch).

Synthetic data only: invented products on example.com.
"""

from __future__ import annotations

import sys
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

ORG, KB, ART = "org-test", "kb-test", "00000000-0000-4000-8000-000000000001"
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
    naming = cheap_layer.cheap_fields(_enriched(NAMING), GROUPS, [], [])
    other = cheap_layer.cheap_fields(_enriched(OTHER), GROUPS, [], [])

    assert naming["context_prefix"] == f"Context.\n{ALIAS}"
    assert naming["text_enriched"] == f"Context.\n\n{NAMING}\n\n{ALIAS}"
    assert (other["context_prefix"], other["text_enriched"]) == ("Context.", f"Context.\n\n{OTHER}")


def test_removing_an_alias_group_removes_its_line_again():
    stored = {**_enriched(NAMING), **cheap_layer.cheap_fields(_enriched(NAMING), GROUPS, [], [])}

    after = cheap_layer.cheap_fields(stored, [], [], [])

    assert (after["context_prefix"], after["text_enriched"]) == (
        "Context.",
        f"Context.\n\n{NAMING}",
    )


LINKED = "Linked as: fax inbox | e-fax"


def test_anchors_become_a_linked_as_line_at_the_end_of_the_embedded_text_only():
    named = cheap_layer.cheap_fields(_enriched(NAMING), GROUPS, [], ["fax inbox", "e-fax"])

    assert named["context_prefix"] == f"Context.\n{ALIAS}"
    assert named["text_enriched"] == f"Context.\n\n{NAMING}\n\n{ALIAS}\n\n{LINKED}"
    plain = cheap_layer.cheap_fields(_enriched(OTHER), [], [], ["fax inbox", "e-fax"])
    assert (plain["context_prefix"], plain["text_enriched"]) == (
        "Context.",
        f"Context.\n\n{OTHER}\n\n{LINKED}",
    )


def test_a_changed_or_removed_anchor_line_is_replaced_not_stacked():
    stored = _enriched(OTHER, text_enriched=f"Context.\n\n{OTHER}\n\n{LINKED}")

    changed = cheap_layer.cheap_fields(stored, [], [], ["voicemail"])
    removed = cheap_layer.cheap_fields(stored, [], [], [])

    assert changed["text_enriched"] == f"Context.\n\n{OTHER}\n\nLinked as: voicemail"
    assert removed["text_enriched"] == f"Context.\n\n{OTHER}"


def test_the_legacy_raw_anchor_block_goes_but_a_real_alias_line_stays():
    """Before the "Linked as" label, ingest appended raw anchor texts as "Also known
    as:" to the embedded text only. An alias line has a twin in the context prefix."""
    legacy = _enriched(OTHER, text_enriched=f"Context.\n\n{OTHER}\n\nAlso known as: fax inbox")
    legacy_with_alias = _enriched(
        NAMING,
        text_enriched=f"Context.\n\n{NAMING}\n\nAlso known as: fax inbox\n\n{ALIAS}",
        context_prefix=f"Context.\n{ALIAS}",
    )

    assert cheap_layer.cheap_fields(legacy, [], [], [])["text_enriched"] == (f"Context.\n\n{OTHER}")
    after = cheap_layer.cheap_fields(legacy_with_alias, GROUPS, [], [])
    assert after["context_prefix"] == f"Context.\n{ALIAS}"
    assert after["text_enriched"] == f"Context.\n\n{NAMING}\n\n{ALIAS}"


def test_the_layer_version_follows_the_anchor_rules_version(monkeypatch):
    before = cheap_layer.layer_version(GROUPS, RULES)

    monkeypatch.setattr(cheap_layer, "ANCHOR_RULES_VERSION", cheap_layer.ANCHOR_RULES_VERSION + 1)

    assert cheap_layer.layer_version(GROUPS, RULES) != before


def test_the_product_follows_the_first_matching_rule_and_is_absent_without_one():
    rules = [*RULES, {"prefix": "help.example.com/", "product": "Generic"}]

    assert cheap_layer.cheap_fields(SOURCE, [], rules, [])["product"] == "Orbit"
    assert "product" not in cheap_layer.cheap_fields(SOURCE, [], [], [])


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


_real_anchors_by_url = kb_patch._anchors_by_url


@pytest.fixture(autouse=True)
def no_link_graph():
    """kb_patch reads the link graph from Postgres; these tests supply the anchors instead."""
    with patch.object(kb_patch, "_anchors_by_url", AsyncMock(return_value={})):
        yield


def _anchored(anchors_by_url: dict):
    return patch.object(kb_patch, "_anchors_by_url", AsyncMock(return_value=anchors_by_url))


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


async def test_a_failed_batch_never_leaves_new_text_or_version_on_old_vectors(
    mem_client, embedders
):
    """A re-ingest deletes a point between the scroll and the write: the batch
    fails, and what survives is redone by the next run instead of stranded."""
    await mem_client.upsert(qs.COLLECTION, points=[_point(n, NAMING) for n in (1, 2, 3)])
    write = mem_client.batch_update_points

    async def reingest_in_between(*args, **kwargs):
        await mem_client.delete(qs.COLLECTION, points_selector=[2])
        return await write(*args, **kwargs)

    version = cheap_layer.layer_version(GROUPS, RULES)
    new_vector = _unit(_dense([f"Context.\n\n{NAMING}\n\n{ALIAS}"])[0])
    with _settings(GROUPS, RULES):
        with (
            patch.object(mem_client, "batch_update_points", new=reingest_in_between),
            pytest.raises(Exception),  # noqa: B017 - the error type differs per Qdrant client
        ):
            await kb_patch.patch_org(mem_client, ORG, execute=True)
        survivors = await _stored(mem_client)
        assert set(survivors) == {1, 3}
        for point in survivors.values():
            stamped = point.payload.get("cheap_layer_version") == version
            if stamped or point.payload["text_enriched"].endswith(ALIAS):
                assert point.vector["vector_chunk"] == pytest.approx(new_vector)

        await kb_patch.patch_org(mem_client, ORG, execute=True)

    for point in (await _stored(mem_client)).values():
        assert point.payload["cheap_layer_version"] == version
        assert point.vector["vector_chunk"] == pytest.approx(new_vector)


async def test_only_aliases_leaves_the_version_so_a_full_run_still_removes_a_stale_label(
    mem_client, embedders
):
    stale = {"source_url": "https://help.example.com/nl/install", "product": "Orbit"}
    await mem_client.upsert(qs.COLLECTION, points=[_point(1, NAMING, **stale)])
    with _settings(GROUPS, RULES):
        await kb_patch.patch_org(mem_client, ORG, only="aliases", execute=True)
        aliased = (await _stored(mem_client))[1].payload
        assert aliased["text_enriched"].endswith(ALIAS)
        assert "cheap_layer_version" not in aliased

        await kb_patch.patch_org(mem_client, ORG, execute=True)

    after = (await _stored(mem_client))[1].payload
    assert "product" not in after
    assert after["cheap_layer_version"] == cheap_layer.layer_version(GROUPS, RULES)


async def test_the_patch_rewrites_only_the_chunks_whose_anchor_line_changes(mem_client, embedders):
    url_new, url_same = "https://help.example.com/a", "https://help.example.com/b"
    same = _point(2, OTHER, source_url=url_same)
    same.payload["text_enriched"] += "\n\nLinked as: voicemail"
    await mem_client.upsert(qs.COLLECTION, points=[_point(1, OTHER, source_url=url_new), same])
    anchors = {url_new: ["fax inbox"], url_same: ["voicemail"]}
    dense, _ = embedders
    new_text = f"Context.\n\n{OTHER}\n\nLinked as: fax inbox"

    with _settings([], []), _anchored(anchors):
        dry = await kb_patch.patch_org(mem_client, ORG, execute=False)
        assert dry[KB].changes["text_enriched"] == 1
        assert [(pid, new) for pid, f, _, new in dry[KB].examples if f == "text_enriched"] == [
            (1, new_text)
        ]
        dense.assert_not_awaited()

        done = await kb_patch.patch_org(mem_client, ORG, execute=True)

    assert (done[KB].written, done[KB].reembedded) == (2, 1)
    dense.assert_awaited_once_with([new_text])


async def test_anchors_use_the_crawled_text_then_the_artifact_text_and_skip_a_chunk_without_a_url():
    points = [
        PointStruct(id=n, vector=[0.0], payload=payload)
        for n, payload in enumerate(
            [
                {"source_url": "https://help.example.com/crawled", "artifact_id": ART},
                {"source_url": "https://help.example.com/uploaded", "artifact_id": ART},
                {"text": "no page"},
            ]
        )
    ]
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value="artifact body")
    crawled = AsyncMock(
        side_effect=lambda _c, _o, _k, url: "crawl body" if url.endswith("crawled") else None
    )
    graph = AsyncMock(return_value=["anchor"])

    class _Scope:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return None

    with (
        patch.object(kb_patch, "tenant_scoped_connection", lambda _org: _Scope()),
        patch.object(kb_patch.pg_store, "get_crawled_page_markdown", crawled),
        patch.object(kb_patch.link_graph, "get_anchor_texts", graph),
    ):
        result = await _real_anchors_by_url(ORG, KB, points)

    assert result == {
        "https://help.example.com/crawled": ["anchor"],
        "https://help.example.com/uploaded": ["anchor"],
    }
    assert [call.args[4] for call in graph.await_args_list] == ["crawl body", "artifact body"]


def test_execute_without_ingest_ok_writes_nothing_and_exits_2(monkeypatch):
    monkeypatch.delenv("INGEST_OK", raising=False)
    monkeypatch.setattr(sys, "argv", ["kb_patch", "--org", ORG, "--execute"])
    run = AsyncMock()
    with patch.object(kb_patch, "patch_org", run), pytest.raises(SystemExit) as stopped:
        kb_patch.main()

    assert stopped.value.code == 2
    run.assert_not_awaited()


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
