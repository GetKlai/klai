"""Chunk-size probe: does the gold sentence reach the chat when children are cut smaller?

Builds a synthetic help-centre corpus (fictional products, example.com URLs, one
seeded generator, no source data), cuts it with two or more chunkers, writes each
arm's children to a scratch Qdrant collection with the production vector layout,
and asks one question per gold fact through the production retrieval shape:
dense + sparse legs fused with RRF (60 candidates), the Infinity reranker over all
60, one child per parent and at most two per document, then the top k.

It answers "is the fact in the passages the chat gets, and how much text is that",
for the chunking alone. It does not run retrieval-api (no router, graph, link
expansion, quality boost), so it says nothing about the window; that is measured
on the real cases. The production collection is never written.

Run it in a throwaway clone of the knowledge-ingest container (same image, env and
networks), with this file, the candidate package and the old chunker mounted:

    RERANKER_URL=<infinity base url> python chunk_size_probe.py --seed 7 \
        --old-chunker /old/chunker.py --org-id <org>

Arms: ``old-ingest`` (2000-char children, 200 overlap: what the base ingest cut
for kb_article), ``old-enriched`` (1200/200: what enrichment re-cut), ``new``
(the current chunker defaults, kb_article profile).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import random
import statistics
import uuid

import httpx
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from knowledge_ingest import chunker as new_chunker
from knowledge_ingest import embedder, sparse_embedder
from knowledge_ingest.content_profiles import child_chunk_chars
from knowledge_ingest.qdrant_store import get_client

_SYLLABLES = [
    "zor",
    "bex",
    "quil",
    "mar",
    "tano",
    "vell",
    "dru",
    "kisp",
    "orin",
    "feld",
    "nuvo",
    "tay",
]
_SETTINGS = [
    "session timeout",
    "upload size limit",
    "retention period",
    "retry interval",
    "queue length",
    "export batch size",
    "login attempt limit",
    "archive delay",
    "sync window",
    "report history",
    "alert threshold",
    "backup frequency",
    "idle lock",
    "attachment limit",
    "cache lifetime",
    "digest interval",
    "audit log length",
    "trash period",
    "invite expiry",
    "reminder lead time",
]
_UNITS = ["minutes", "hours", "days", "items", "megabytes", "attempts"]
_FILLER = [
    "Changes take effect after the next sign in.",
    "Only administrators can change this value.",
    "The default is shown on the settings page.",
    "Existing users keep the value they had before.",
    "A shorter value is stricter, a longer one is more relaxed.",
    "The change is recorded in the activity log.",
    "If the value is left empty, the standard value applies.",
    "Support can reset this to the standard value on request.",
    "This applies to every workspace in the account.",
    "Read the release notes before changing it for a large team.",
    "The setting is found under the advanced tab.",
    "Values outside the allowed range are rejected with a message.",
]
_SECTIONS = ["Account", "Security"]
GENERATOR = "klai-knowledge-ingest/scripts/chunk_size_probe.py"
RERANK_FROM = 60
CANDIDATES = 60


def generate(seed: int, products: int, per_article_questions: int):
    """Return (articles, questions). Fully determined by the arguments."""
    rng = random.Random(seed)  # noqa: S311 - a seeded corpus generator, not security
    names: set[str] = set()
    while len(names) < products:
        names.add(rng.choice(_SYLLABLES).title() + rng.choice(_SYLLABLES) + f" {rng.randint(2, 9)}")
    articles, questions = [], []
    for product in sorted(names):
        lines = [f"# {product} help", ""]
        facts = []
        pool = rng.sample(_SETTINGS, len(_SETTINGS))
        for i, section in enumerate(_SECTIONS):
            lines += [f"## {section}", ""]
            for setting in pool[i * 10 : i * 10 + 10]:
                value = f"{rng.randint(5, 990)} {rng.choice(_UNITS)}"
                fact = f"In {product}, the {setting} is {value}."
                sentences = [*rng.sample(_FILLER, 4), fact, *rng.sample(_FILLER, 3)]
                # The fact sits at a random place in its paragraph, not always in the middle.
                rng.shuffle(sentences)
                lines += [
                    f"The {setting} controls how {product} behaves here. " + " ".join(sentences),
                    "",
                ]
                facts.append((setting, fact))
        path = f"probe/{product.lower().replace(' ', '-')}"
        articles.append((path, f"{product} help", "\n".join(lines)))
        for setting, fact in rng.sample(facts, per_article_questions):
            questions.append(
                {"q": f"What is the {setting} in {product}?", "gold": fact, "path": path}
            )
    return articles, questions


def load_old_chunker(path: str):
    spec = importlib.util.spec_from_file_location("old_chunker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cut(module, content: str, size: int | None, overlap: int | None):
    kwargs = {}
    if size is not None:
        kwargs["child_size"] = size
    if overlap is not None:
        kwargs["child_overlap"] = overlap
    return module.chunk_markdown_with_parents(content, **kwargs)


async def rerank(query: str, passages: list[str]) -> list[int]:
    url = os.environ["RERANKER_URL"]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{url}/v1/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": query,
                "documents": passages,
                "top_n": len(passages),
            },
        )
        resp.raise_for_status()
    results = resp.json()["results"]
    return [
        r["index"]
        for r in sorted(results, key=lambda r: -r.get("relevance_score", r.get("score", 0)))
    ]


async def build_arm(client, collection, arm, module, size, overlap, articles, org_id):
    texts, payloads = [], []
    for path, title, content in articles:
        children, _ = cut(module, content, size, overlap)
        for child in children:
            texts.append(child.text)
            payloads.append(
                {
                    "org_id": org_id,
                    "kb_slug": arm,
                    "path": path,
                    "title": title,
                    "source_url": f"https://example.com/{path}",
                    "text": child.text,
                    "parent": f"{path}#{child.parent_index}",
                }
            )
    dense = await embedder.embed(texts)
    sparse = await sparse_embedder.embed_sparse_batch(texts)
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector={"vector_chunk": d, **({"vector_sparse": s} if s is not None else {})},
            payload=p,
        )
        for d, s, p in zip(dense, sparse, payloads, strict=True)
    ]
    for i in range(0, len(points), 128):
        await client.upsert(collection, points=points[i : i + 128])
    return {"chunks": len(texts), "mean_chars": round(statistics.mean(len(t) for t in texts))}


async def ask(client, collection, arm, org_id, question, ks):
    dense = (await embedder.embed([question["q"]]))[0]
    sparse = (await sparse_embedder.embed_sparse_batch([question["q"]]))[0]
    scope = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=arm)),
        ]
    )
    prefetch = [Prefetch(query=dense, using="vector_chunk", limit=CANDIDATES * 4, filter=scope)]
    if sparse is not None:
        prefetch.append(
            Prefetch(query=sparse, using="vector_sparse", limit=CANDIDATES * 4, filter=scope)
        )
    result = await client.query_points(
        collection,
        prefetch=prefetch,
        query=FusionQuery(fusion=Fusion.RRF),
        limit=CANDIDATES,
        with_payload=True,
    )
    candidates = [p.payload for p in result.points]
    gold = question["gold"]
    out = {"in_candidates": any(gold in c["text"] for c in candidates)}
    top = candidates[:RERANK_FROM]
    order = await rerank(question["q"], [c["text"] for c in top]) if top else []
    ranked = [top[i] for i in order]
    out["in_reranked"] = any(gold in c["text"] for c in ranked)
    seen_parent, per_path, served = set(), {}, []
    for c in ranked:
        if c["parent"] in seen_parent or per_path.get(c["path"], 0) >= 2:
            continue
        seen_parent.add(c["parent"])
        per_path[c["path"]] = per_path.get(c["path"], 0) + 1
        served.append(c)
    for k in ks:
        top_k = served[:k]
        out[f"served_{k}"] = any(gold in c["text"] for c in top_k)
        out[f"chars_{k}"] = sum(len(c["text"]) for c in top_k)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--products", type=int, default=40)
    parser.add_argument("--per-article", type=int, default=5)
    parser.add_argument(
        "--old-chunker", required=True, help="chunker.py of the code before this change"
    )
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--keep", action="store_true", help="keep the scratch collection")
    args = parser.parse_args()

    articles, questions = generate(args.seed, args.products, args.per_article)
    old = load_old_chunker(args.old_chunker)
    arms = {
        "old-ingest": (old, 2000, 200),
        "old-enriched": (old, 1200, 200),
        "new": (new_chunker, child_chunk_chars("kb_article"), None),
    }
    collection = f"chunkprobe_{args.seed}_{uuid.uuid4().hex[:6]}"
    client = get_client()
    await client.create_collection(
        collection,
        vectors_config={
            "vector_chunk": VectorParams(size=embedder.EMBED_DIM, distance=Distance.COSINE)
        },
        sparse_vectors_config={
            "vector_sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))
        },
    )
    summary = {
        "synthetic-data": f"generator={GENERATOR} seed={args.seed}",
        "articles": len(articles),
        "questions": len(questions),
        "arms": {},
    }
    try:
        for arm, (module, size, overlap) in arms.items():
            info = await build_arm(
                client, collection, arm, module, size, overlap, articles, args.org_id
            )
            rows = [
                await ask(client, collection, arm, args.org_id, q, (4, 8, 20)) for q in questions
            ]
            n = len(rows)
            info |= {
                "recall_candidates_60": round(sum(r["in_candidates"] for r in rows) / n, 3),
                "recall_reranked": round(sum(r["in_reranked"] for r in rows) / n, 3),
            }
            for k in (4, 8, 20):
                info[f"recall_served_{k}"] = round(sum(r[f"served_{k}"] for r in rows) / n, 3)
                info[f"median_chars_served_{k}"] = int(
                    statistics.median(r[f"chars_{k}"] for r in rows)
                )
            summary["arms"][arm] = info
    finally:
        if not args.keep:
            await client.delete_collection(collection)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
