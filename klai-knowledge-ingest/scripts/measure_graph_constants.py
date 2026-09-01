"""REQ-6 probe: measure the post-ANN graph-build constants, today, on the server.

SPEC-GRAPH-SCALE-001 REQ-6. The pre-flight model is ``T = a*C + b*C**2``; under
ANN the interim assumption is ``b ~= 0``. This probe replaces that assumption
with a measurement, without waiting for organic throughput: it ingests the SAME
set of real documents through the FULL production pipeline (LLM extraction,
resolution, graph writes) twice —

  run A: into an EMPTY graph          -> marginal cost ~= a
  run B: into a ~30k-edge graph copy  -> marginal cost ~= a + 2*b*C0

so ``b = (tB - tA) * docs_per_mchar / (2 * C0)`` with C0 the Mchar-equivalent
of the pre-loaded graph. Same documents, same LLM, same embedder, same
FalkorDB version — the only variable is graph size.

MUST run against a THROWAWAY FalkorDB (labelled ``klai.adhoc``), never against
production data (``FALKORDB_HOST`` env override). It spends real LLM budget
(~26 calls/episode) — keep ``--docs`` modest and concurrency at 1.

Usage (operator one-shot, from inside the knowledge-ingest container):

    docker exec \
      -e FALKORDB_HOST=<probe-container> -e GRAPHITI_EPISODE_DELAY=0 \
      klai-core-knowledge-ingest-1 \
      python -m scripts.measure_graph_constants \
        --source-org <org_id> --big-graph <probe-graph-name> --docs 10

Prints per-run stats and the derived (a, b) constants. Read-only towards
production stores: documents are read from Qdrant; nothing is written outside
the probe FalkorDB (``entity_graph_data`` is collected and discarded, so no
Qdrant payload writes happen).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import structlog
from qdrant_client import AsyncQdrantClient

from knowledge_ingest.config import settings
from knowledge_ingest.enrichment_policy import graph_episode_skip_reason
from knowledge_ingest.episode_text import split_episode_text
from knowledge_ingest.graph import EntityGraphData, ingest_episode

logger = structlog.get_logger()

DOCS_PER_MCHAR = 103.1  # REQ-6 measurement: 97,012 chars / 10 docs
EDGES_PER_MCHAR = 2600.0


async def _load_documents(source_org: str, count: int) -> list[tuple[str, str]]:
    """Fetch ``count`` mid-sized real documents (joined chunk text) from Qdrant."""
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    chunks: dict[str, list[str]] = {}
    offset = None
    while True:
        batch, offset = await qdrant.scroll(
            collection_name=settings.qdrant_collection,
            offset=offset,
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        for pt in batch:
            payload = pt.payload or {}
            if payload.get("org_id") != source_org:
                continue
            aid, text = payload.get("artifact_id"), payload.get("text")
            if aid and text:
                chunks.setdefault(aid, []).append(text)
        if offset is None:
            break

    docs: list[tuple[str, str]] = []
    for aid, parts in chunks.items():
        full = "\n\n".join(parts)
        # Mid-sized, single-episode, non-navigation documents only: one
        # episode per doc keeps the per-episode arithmetic clean.
        if 6_000 <= len(full) <= 15_000 and not graph_episode_skip_reason(full):
            if len(split_episode_text(full)) == 1:
                docs.append((aid, full))
        if len(docs) >= count:
            break
    if len(docs) < count:
        raise RuntimeError(f"only {len(docs)} suitable documents found for org {source_org}")
    return docs


async def _run(graph_name: str, docs: list[tuple[str, str]]) -> list[float]:
    times: list[float] = []
    for i, (aid, text) in enumerate(docs, 1):
        t0 = time.perf_counter()
        episode_id = await ingest_episode(
            artifact_id=f"probe-{graph_name}-{aid}",
            document_text=text,
            org_id=graph_name,
            content_type="text",
            belief_time_start=int(time.time()),
            entity_graph_data=EntityGraphData(),  # collected, never flushed
        )
        elapsed = time.perf_counter() - t0
        if episode_id is None:
            raise RuntimeError(f"episode {i} failed on graph {graph_name}")
        times.append(elapsed)
        logger.info(
            "probe_episode_done", graph=graph_name, doc=i, of=len(docs), seconds=round(elapsed, 1)
        )
    return times


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-org", required=True, help="org whose documents to sample")
    parser.add_argument("--big-graph", required=True, help="probe graph name preloaded with edges")
    parser.add_argument("--empty-graph", default="probe-empty")
    parser.add_argument("--docs", type=int, default=10)
    args = parser.parse_args()

    if settings.falkordb_host == "falkordb":
        logger.error("probe_refused_production_falkordb", host=settings.falkordb_host)
        return 1

    from falkordb import FalkorDB

    client = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
    big_edges = (
        client.select_graph(args.big_graph)
        .ro_query("MATCH ()-[r:RELATES_TO]->() RETURN count(r)")
        .result_set[0][0]
    )
    c0 = big_edges / EDGES_PER_MCHAR
    logger.info("probe_start", big_graph_edges=big_edges, c0_mchar_equivalent=round(c0, 2))

    docs = await _load_documents(args.source_org, args.docs)
    total_chars = sum(len(t) for _, t in docs)
    logger.info("probe_documents", count=len(docs), total_chars=total_chars)

    times_a = await _run(args.empty_graph, docs)
    times_b = await _run(args.big_graph, docs)

    # Drop the first episode of each run: it carries one-off cost the steady
    # state does not (lazy Graphiti client construction, embedder /info probe,
    # index creation on a fresh graph). Measured on the first attempt: 95s and
    # 108s for episodes 1-2 against 35s for episode 3 on the SAME graph. Left
    # in, that warm-up inflates run A and biases the derived b toward zero —
    # i.e. toward the very assumption this probe exists to test.
    warm_a, warm_b = times_a[1:] or times_a, times_b[1:] or times_b

    mean_a, mean_b = statistics.mean(warm_a), statistics.mean(warm_b)
    stdev_a = statistics.stdev(warm_a) if len(warm_a) > 1 else 0.0
    stdev_b = statistics.stdev(warm_b) if len(warm_b) > 1 else 0.0
    print(f"raw incl. warm-up: A={statistics.mean(times_a):.1f}s B={statistics.mean(times_b):.1f}s")

    # a from run A (near-empty marginal cost), converted to hours/Mchar and
    # including the production inter-episode delay the probe disabled.
    per_doc_a_prod = mean_a + settings.graphiti_episode_delay
    a_hours = per_doc_a_prod / 3600.0 * DOCS_PER_MCHAR
    # b from the marginal-cost difference at C0.
    delta = mean_b - mean_a
    b_hours = (delta / 3600.0 * DOCS_PER_MCHAR) / (2.0 * c0) if c0 > 0 else 0.0

    print(f"run A (empty):  mean={mean_a:.1f}s stdev={stdev_a:.1f}s n={len(warm_a)}")
    print(f"run B ({big_edges} edges): mean={mean_b:.1f}s stdev={stdev_b:.1f}s n={len(warm_b)}")
    print(f"delta per doc: {delta:+.1f}s (pooled stdev ~{max(stdev_a, stdev_b):.1f}s)")
    print(f"derived a = {a_hours:.2f} h/Mchar (incl. {settings.graphiti_episode_delay}s delay)")
    print(f"derived b_ann = {b_hours:.4f} h/Mchar^2 at C0={c0:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
