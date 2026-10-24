"""Product label and text fingerprint per chunk (knowledge-base plan, step 7).

Synthetic data only: fictional products on example.com.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest.product_label import derive_product
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
        # A crawled page: the rule's prefix starts its host plus URL path.
        (
            {
                "source_type": "crawl",
                "source_url": "https://help.example.com/nl/orbit-desk/forwarding",
            },
            [{"prefix": "HELP.example.com/nl/orbit-desk", "product": "Orbit Desk"}],
            "Orbit Desk",
        ),
        # Every other source: the rule's prefix starts its path.
        (
            {
                "source_type": "connector",
                "path": "ORBIT/123456",
                "source_url": "https://wiki.example.com/x/y",
            },
            [{"prefix": "orbit/", "product": "Orbit"}],
            "Orbit",
        ),
    ],
)
def test_a_matching_rule_of_the_knowledge_base_names_the_product(payload, rules, product):
    assert derive_product(payload, rules) == product


def test_a_document_without_a_matching_rule_gets_no_label():
    """A first URL segment is a language or section code as often as a product
    (/nl/, /en/, /support/), so nothing but a rule names one."""
    rules = [{"prefix": "help.example.com/zephyr/", "product": "Zephyr"}]
    crawled = {"source_type": "crawl", "source_url": "https://help.example.com/nl/articles/1"}

    assert derive_product(crawled, rules) == ""
    assert derive_product({"source_type": "connector", "path": "ORBIT/123456"}, rules) == ""


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
            point(5, ORG, "diary", visibility="private"),
            point(6, "org-other", "nimbus"),
        ],
    )
    assert await qs.org_products(ORG) == ["orbit", "zephyr"]
