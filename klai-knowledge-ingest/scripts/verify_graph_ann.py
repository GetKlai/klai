"""Create + verify the ANN vector indexes per tenant graph, in one shot.

SPEC-GRAPH-SCALE-001 REQ-5. The ANN candidate search (REQ-4) ships behind
``GRAPH_ANN_ENABLED`` with a per-query fallback to the brute-force scan, so
enabling the flag before indexes exist is safe — but the fast path only
starts paying once the indexes are OPERATIONAL, and nobody should take the
recall of an approximate index on faith. This script settles both, on the
server, against live data, without any historical measurement campaign:

1. creates both vector indexes (idempotent) on each selected tenant graph;
2. waits until they are OPERATIONAL;
3. samples real embeddings and compares the EXACT queries the patched code
   runs (same k over-fetch) against the brute-force ground truth;
4. prints recall + latency per graph and exits non-zero when recall falls
   below the gate, so it can guard the rollout.

Usage (operator one-shot, read-only apart from index creation):

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.verify_graph_ann --org-id <org_id>
    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.verify_graph_ann --all

``--all`` selects every graph whose name is a bare Zitadel org id (all
digits); backups (``backup-*``) and ``default_db`` are skipped on purpose —
indexing a backup buys nothing.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import structlog
from klai_graphiti_compat import GRAPHITI_VECTOR_DIMENSION

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Mirrors the patched fast path (klai_graphiti_compat): candidate limits from
# graphiti 0.29.3 and the 4x over-fetch the patch applies.
EDGE_LIMIT = 10  # RELEVANT_SCHEMA_LIMIT
NODE_LIMIT = 15  # NODE_DEDUP_CANDIDATE_LIMIT
OVERFETCH = 4
INDEX_BUILD_TIMEOUT_S = 600.0

_INDEX_DDL = (
    "CREATE VECTOR INDEX FOR ()-[e:RELATES_TO]->() ON (e.fact_embedding) "
    f"OPTIONS {{dimension:{GRAPHITI_VECTOR_DIMENSION}, similarityFunction:'cosine'}}",
    "CREATE VECTOR INDEX FOR (n:Entity) ON (n.name_embedding) "
    f"OPTIONS {{dimension:{GRAPHITI_VECTOR_DIMENSION}, similarityFunction:'cosine'}}",
)


def _client():
    from falkordb import FalkorDB

    return FalkorDB(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        socket_connect_timeout=2.0,
        socket_timeout=30.0,
    )


def _ensure_indexes(graph) -> None:
    for ddl in _INDEX_DDL:
        try:
            graph.query(ddl)
        except Exception as exc:
            if "already indexed" in str(exc).lower():
                continue
            raise


def _wait_operational(graph, deadline_s: float = INDEX_BUILD_TIMEOUT_S) -> None:
    t0 = time.monotonic()
    while True:
        rows = graph.ro_query(
            "CALL db.indexes() YIELD label, properties, types, status "
            "RETURN label, properties, types, status"
        ).result_set
        pending = [r for r in rows if "VECTOR" in str(r[2]) and "OPERATIONAL" not in str(r[3])]
        if not pending:
            return
        if time.monotonic() - t0 > deadline_s:
            raise TimeoutError(f"vector index build not OPERATIONAL after {deadline_s}s: {pending}")
        time.sleep(2.0)


def _sample_vectors(graph, cypher: str, samples: int) -> list[list[float]]:
    rows = graph.ro_query(cypher, params={"n": samples}).result_set
    return [list(r[0]) for r in rows if r[0] is not None]


def _recall_run(graph, vectors, index_cypher, truth_cypher, k, limit):
    recalls, ann_ms, scan_ms = [], [], []
    for vec in vectors:
        t = time.perf_counter()
        index_params = {"v": vec, "k": k, "limit": limit}
        got_rows = graph.ro_query(index_cypher, params=index_params).result_set
        ann_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        truth_rows = graph.ro_query(truth_cypher, params={"v": vec, "limit": limit}).result_set
        scan_ms.append((time.perf_counter() - t) * 1000)

        truth = {r[0] for r in truth_rows}
        got = {r[0] for r in got_rows}
        if truth:
            recalls.append(len(truth & got) / len(truth))
    return recalls, ann_ms, scan_ms


def verify_graph(graph_name: str, samples: int) -> dict[str, float] | None:
    graph = _client().select_graph(graph_name)
    edge_count = graph.ro_query("MATCH ()-[r:RELATES_TO]->() RETURN count(r)").result_set[0][0]
    if not edge_count:
        logger.info("graph_ann_verify_skipped_empty", graph=graph_name)
        return None

    _ensure_indexes(graph)
    _wait_operational(graph)

    edge_vecs = _sample_vectors(
        graph,
        "MATCH ()-[e:RELATES_TO]->() WHERE e.fact_embedding IS NOT NULL "
        "RETURN e.fact_embedding LIMIT $n",
        samples,
    )
    node_vecs = _sample_vectors(
        graph,
        "MATCH (m:Entity) WHERE m.name_embedding IS NOT NULL RETURN m.name_embedding LIMIT $n",
        samples,
    )

    e_recall, e_ann, e_scan = _recall_run(
        graph,
        edge_vecs,
        "CALL db.idx.vector.queryRelationships('RELATES_TO','fact_embedding',$k,vecf32($v)) "
        "YIELD relationship, score RETURN relationship.uuid ORDER BY score LIMIT $limit",
        "MATCH ()-[e:RELATES_TO]->() "
        "WITH e, (2 - vec.cosineDistance(e.fact_embedding, vecf32($v)))/2 AS score "
        "RETURN e.uuid ORDER BY score DESC LIMIT $limit",
        k=OVERFETCH * EDGE_LIMIT,
        limit=EDGE_LIMIT,
    )
    n_recall, n_ann, n_scan = _recall_run(
        graph,
        node_vecs,
        "CALL db.idx.vector.queryNodes('Entity','name_embedding',$k,vecf32($v)) "
        "YIELD node, score RETURN node.uuid ORDER BY score LIMIT $limit",
        "MATCH (m:Entity) WHERE m.name_embedding IS NOT NULL "
        "WITH m, (2 - vec.cosineDistance(m.name_embedding, vecf32($v)))/2 AS score "
        "RETURN m.uuid ORDER BY score DESC LIMIT $limit",
        k=OVERFETCH * NODE_LIMIT,
        limit=NODE_LIMIT,
    )

    result = {
        "edges": int(edge_count),
        "edge_recall_mean": round(statistics.mean(e_recall), 4) if e_recall else 1.0,
        "edge_recall_min": round(min(e_recall), 4) if e_recall else 1.0,
        "node_recall_mean": round(statistics.mean(n_recall), 4) if n_recall else 1.0,
        "node_recall_min": round(min(n_recall), 4) if n_recall else 1.0,
        "edge_ann_ms": round(statistics.median(e_ann), 1) if e_ann else 0.0,
        "edge_scan_ms": round(statistics.median(e_scan), 1) if e_scan else 0.0,
        "node_ann_ms": round(statistics.median(n_ann), 1) if n_ann else 0.0,
        "node_scan_ms": round(statistics.median(n_scan), 1) if n_scan else 0.0,
    }
    logger.info("graph_ann_verified", graph=graph_name, **result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", action="append", dest="org_ids", default=None)
    parser.add_argument("--all", action="store_true", help="every all-digit tenant graph")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument(
        "--min-recall", type=float, default=0.95, help="gate on MEAN recall per path"
    )
    args = parser.parse_args()

    if bool(args.org_ids) == bool(args.all):
        parser.error("pass either --org-id (repeatable) or --all")

    if args.all:
        names = [g for g in _client().list_graphs() if str(g).isdigit()]
    else:
        names = args.org_ids

    failed: list[str] = []
    for name in names:
        try:
            result = verify_graph(name, args.samples)
        except Exception:
            logger.exception("graph_ann_verify_error", graph=name)
            failed.append(name)
            continue
        if result is None:
            continue
        if (
            result["edge_recall_mean"] < args.min_recall
            or result["node_recall_mean"] < args.min_recall
        ):
            failed.append(name)

    if failed:
        logger.error("graph_ann_verify_failed", graphs=failed, min_recall=args.min_recall)
        return 1
    logger.info("graph_ann_verify_complete", graphs=len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
