"""Product label and text fingerprint per chunk, and their backfill (knowledge-base plan, step 7).

Synthetic data only: fictional products on example.com.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest.product_label import derive_product
from knowledge_ingest.scripts import product_backfill
from knowledge_ingest.utils.content_fingerprint import compute_minhash

ORG, KB = "org-test", "kb-test"

_ARTICLE = (
    "Open the {p} app and go to Settings. Choose Calls and then Forwarding. "
    "Enter the number you want "
    "your calls forwarded to and press Save. "
    "The {p} app shows a green bar while forwarding is on. To stop "
    "forwarding, open Settings again, choose Calls, switch Forwarding off and press Save. "
    "When the number "
    "is busy the call goes to your voicemail instead, "
    "which you set up under Settings and Voicemail."
)


@pytest.mark.parametrize(
    ("payload", "rules", "product"),
    [
        # Crawled site: the first segment of a nested URL path.
        (
            {"source_type": "crawl", "source_url": "https://help.example.com/orbit/forwarding"},
            [],
            "orbit",
        ),
        # A page directly under the root names itself, not a product.
        ({"source_type": "crawl", "source_url": "https://help.example.com/forwarding"}, [], ""),
        # A rule of the knowledge base wins over the default, matched on host plus path.
        (
            {
                "source_type": "crawl",
                "source_url": "https://help.example.com/nl/orbit-desk/forwarding",
            },
            [{"prefix": "help.example.com/nl/orbit-desk", "product": "Orbit Desk"}],
            "Orbit Desk",
        ),
        (
            {"source_type": "crawl", "source_url": "https://zephyr.example.com/start"},
            [{"prefix": "ZEPHYR.example.com/", "product": "Zephyr"}],
            "Zephyr",
        ),
        # Connector: the folder or space the document sits in.
        (
            {
                "source_type": "connector",
                "path": "ORBIT/123456",
                "source_url": "https://wiki.example.com/x/y",
            },
            [],
            "ORBIT",
        ),
        ({"source_type": "connector", "path": "Forwarding in the app"}, [], ""),
        # An upload has no folder; only a rule can label it.
        ({"source_type": "upload", "path": "orbit-manual.pdf"}, [], ""),
        (
            {"source_type": "upload", "path": "orbit-manual.pdf"},
            [{"prefix": "orbit-", "product": "Orbit"}],
            "Orbit",
        ),
    ],
)
def test_product_is_derived_from_url_rules_and_connector_folders(payload, rules, product):
    assert derive_product(payload, rules) == product


def _share_equal(a: str, b: str) -> float:
    x, y = bytes.fromhex(a), bytes.fromhex(b)
    return sum(p == q for p, q in zip(x, y, strict=True)) / len(x)


def test_fingerprint_matches_a_lookalike_of_another_product_and_not_another_article():
    orbit = compute_minhash(_ARTICLE.format(p="Orbit"))
    zephyr = compute_minhash(_ARTICLE.format(p="Zephyr"))
    other = compute_minhash(
        "Your invoice arrives on the first working day of the month. "
        "Download it as a PDF under Billing, "
        "where you also change the address it is sent to and the way you pay. A credit note for a "
        "cancelled subscription appears in the same list within five working days."
    )
    assert _share_equal(orbit, zephyr) >= 0.7
    assert _share_equal(orbit, other) < 0.2
    assert compute_minhash("") == ""


@pytest.fixture
async def mem_client():
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        qs.COLLECTION,
        vectors_config={"vector_chunk": VectorParams(size=2, distance=Distance.COSINE)},
    )
    with patch.object(qs, "get_client", return_value=client):
        yield client
    await client.close()


async def test_raw_upsert_stores_the_text_fingerprint(mem_client):
    with patch.object(qs.sparse_embedder, "embed_sparse_batch", AsyncMock(return_value=[None])):
        await qs.upsert_chunks(
            org_id=ORG,
            kb_slug=KB,
            path="orbit/forwarding",
            chunks=[_ARTICLE.format(p="Orbit")],
            vectors=[[1.0, 0.0]],
            artifact_id="art-1",
            extra_payload={"product": "orbit"},
        )
    (point,), _ = await mem_client.scroll(qs.COLLECTION, with_payload=True)
    assert point.payload["product"] == "orbit"
    assert point.payload["text_fingerprint"] == compute_minhash(_ARTICLE.format(p="Orbit"))


async def test_product_list_holds_only_the_orgs_shared_labels(mem_client):
    def point(n: int, org: str, product: str, visibility: str = "internal") -> PointStruct:
        payload = {"org_id": org, "kb_slug": KB, "product": product, "visibility": visibility}
        return PointStruct(id=n, vector={"vector_chunk": [1.0, 0.0]}, payload=payload)

    await mem_client.upsert(
        qs.COLLECTION,
        points=[
            point(1, ORG, "orbit"),
            point(2, ORG, "orbit"),
            point(3, ORG, "zephyr"),
            point(4, ORG, ""),
            point(5, ORG, "diary", visibility="private"),
            point(6, "org-other", "nimbus"),
        ],
    )
    assert await qs.org_products(ORG) == ["orbit", "zephyr"]


async def test_backfill_dry_run_counts_and_real_run_writes_only_missing_labels(mem_client):
    def point(n: int, payload: dict) -> PointStruct:
        base = {"org_id": ORG, "kb_slug": KB, "text": _ARTICLE.format(p=f"P{n}")}
        return PointStruct(id=n, vector={"vector_chunk": [1.0, 0.0]}, payload={**base, **payload})

    await mem_client.upsert(
        qs.COLLECTION,
        points=[
            point(1, {"source_type": "crawl", "source_url": "https://help.example.com/orbit/a"}),
            point(2, {"source_type": "crawl", "source_url": "https://help.example.com/zephyr/b"}),
            point(3, {"source_type": "upload", "path": "notes.md"}),
            # Already labelled: left alone, also when the rules would say otherwise now.
            point(
                4,
                {
                    "source_type": "crawl",
                    "source_url": "https://help.example.com/orbit/c",
                    "product": "kept",
                },
            ),
            # Another tenant is out of scope.
            point(
                5,
                {
                    "org_id": "org-other",
                    "source_type": "crawl",
                    "source_url": "https://help.example.com/orbit/d",
                },
            ),
        ],
    )
    rules = AsyncMock(return_value=[])
    with patch.object(product_backfill.kb_config, "get_kb_product_rules", rules):
        dry, labels = await product_backfill.backfill_products(mem_client, ORG, execute=False)
        assert dry["missing"] == 3
        assert dry["written"] == 0
        assert labels == {"orbit": 1, "zephyr": 1, "": 1}
        untouched = {
            p.id: p.payload for p in (await mem_client.scroll(qs.COLLECTION, with_payload=True))[0]
        }
        assert "product" not in untouched[1]

        done, _ = await product_backfill.backfill_products(mem_client, ORG, execute=True)
        assert done["written"] == 3
        again, _ = await product_backfill.backfill_products(mem_client, ORG, execute=True)
        assert again["missing"] == 0

    after = {
        p.id: p.payload for p in (await mem_client.scroll(qs.COLLECTION, with_payload=True))[0]
    }
    assert after[1]["product"] == "orbit"
    assert after[1]["text_fingerprint"] == compute_minhash(after[1]["text"])
    assert after[2]["product"] == "zephyr"
    assert after[3]["product"] == ""
    assert after[4]["product"] == "kept"
    assert "text_fingerprint" not in after[4]
    assert "product" not in after[5]
