# SPEC-GRAPH-SCALE-001 REQ-3 — ANN backend spike report

Date: 2026-09-01 · Operator: Claude (Fable), authorized by Mark Vletter
Test bed: local FalkorDB v4.20.3 (Docker, Apple Silicon) loaded from a copy
of the production RDB (nightly dump); all experiments ran on a **copy** of
the largest tenant graph (30,236 `RELATES_TO` edges, 12,861 `Entity` nodes,
bge-m3 1024-dim embeddings). The pristine copy was only read. Production was
not touched.

## Verdict: option 1a — FalkorDB native vector index, via `SearchInterface`

All three decision measurements from REQ-3 pass for the native index; the
Qdrant sidecar (1b) loses on the graph-fetch hop it cannot avoid without
extra engineering, plus the dual-write surface it was already priced at.

## A. Latency and score semantics (static graph, 30,236 edges)

| Path | median | mean | max | n |
|---|---|---|---|---|
| Brute force (graphiti's exact query shape) | 50.6 ms | 51.0 ms | 54.5 ms | 20 |
| `db.idx.vector.queryRelationships` top-10 | **1.2 ms** | 1.2 ms | 1.6 ms | 20 |

- **~42× at 30k edges**, and the gap grows linearly with graph size (below).
- Empty results: 0/20. Recall@10 vs brute-force ground truth: mean **0.995**,
  min 0.90.
- **Score semantics: cosine DISTANCE, ascending** — self-match returns 0.0,
  other results return varied small values. The historical
  [FalkorDB#525](https://github.com/FalkorDB/FalkorDB/issues/525) always-0
  bug is absent on v4.20.3. Conversion to graphiti's similarity is exactly
  its own formula: `similarity = (2 − distance) / 2`.
- `db.idx.vector.queryNodes` on `Entity.name_embedding`: 1.4 ms, same
  semantics.
- Index build on existing data: node index (12,861 vectors) ~35 s inline;
  edge index (30,236 vectors) async, `OPERATIONAL` shortly after. Per-tenant
  index creation is minutes at worst.

## B. Correctness under writes — the #1287 failure mode does NOT reproduce

Community PR [getzep/graphiti#1287](https://github.com/getzep/graphiti/pull/1287)
was withdrawn (March 2026, older FalkorDB) after measuring 83% empty/wrong
HNSW results under interleaved read/write. Reproduced its pattern at the DB
layer on v4.20.3:

- **B1 — sequential interleaving** (Klai's real ingest pattern,
  `GRAPHITI_MAX_CONCURRENT=1`): 40 episode-like rounds × 18 edge inserts
  (perturbed real embeddings, renormalised), querying the index after every
  batch. Result over 720 inserts: **0 empty results**, 2/720 self-find
  misses (0.3% — a just-inserted vector ranking 11th among near-identical
  synthetic siblings, i.e. ordinary HNSW approximation, not the failure
  mode), recall@10 mean 0.983 (min 0.80, n=120).
- **B2 — true concurrency** (retrieval reads racing ingest writes): 3 query
  threads against 1 insert thread for 30 s: **22,020 index queries, 0
  empty, 0 errors**, while 6,282 edges were inserted concurrently.

## C. Scale headroom — the wall disappears

Grew the copy to **100,238 edges** (the region where the SPEC's tail model
puts brute force at the 5 s timeout on production hardware):

| Path @ 100k edges | median | max |
|---|---|---|
| Index top-10 | **0.9 ms** | 1.8 ms (0/20 empty) |
| Brute force | 172.4 ms | 179.6 ms |

Index latency is flat (0.9 ms at 100k vs 1.2 ms at 30k); brute force grew
3.4× and — scaled by the production-measured 15.6 µs/edge — would average
~1.6 s on core-01 at this size with the tail at the 5 s timeout. The index
removes the edge ceiling as a constraint entirely; the LLM budget becomes
the only build-time limit, as §2 lever 1 predicted.

## D. Qdrant sidecar reference (option 1b)

Loaded all 30,236 real fact embeddings into a local Qdrant (HNSW, cosine):

- Qdrant top-10: median 3.1 ms — fine, as expected.
- **The mandatory hop back into FalkorDB
  (`MATCH ()-[e]->() WHERE e.uuid IN $ids`) measured 180.8 ms median** —
  it is itself an O(E) edge scan, so the naive sidecar reintroduces
  exactly the cost being removed. Avoiding it needs extra engineering
  (endpoint uuids in the Qdrant payload + node-anchored fetches, or edge
  lookup restructuring) on top of the already-priced dual-write/delete
  surface across every delete path in `graph.py`.

Total sidecar path as measured: ~184 ms vs ~1 ms native. 1b remains the
fallback if a native-index defect surfaces later, but it starts behind.

## Caveats (state plainly)

- Hardware: Apple Silicon Mac, not core-01 — absolute latencies are
  flattering; the ratios and all correctness results are what transfer.
  The production-measured 15.6 µs/edge anchors the absolute translation.
- Synthetic writes used perturbed copies of real embeddings; the LLM
  extraction layer was not exercised (irrelevant to index correctness).
- Not reproducing #1287 on v4.20.3 does not prove it can never occur on
  other versions/configs — REQ-5's shadow-verification gate (recall ≥ 0.95,
  ≥ 40 episodes, no empty-result anomalies) stays in place as designed.
- Filtered search: not exercised; the design already routes the one
  filtered case (`edge_uuids` pair lookups) around the vector index.
- Index memory at production scale: not measured; the docs formula puts
  30k+13k vectors at 1024 dims around ~0.2 GB per tenant of this size —
  verify against container RSS during REQ-5 rollout.

## Consequence for REQ-4

Implement the `SearchInterface` against the native index:
`db.idx.vector.queryRelationships('RELATES_TO','fact_embedding',k,…)` /
`queryNodes('Entity','name_embedding',k,…)`, similarity = `(2−score)/2`,
over-fetch (k = 4× limit) + post-filter for residual `SearchFilters`, index
creation added to the per-tenant `ensure_database_initialized` path, behind
`GRAPH_ANN_ENABLED`, wired in both knowledge-ingest and retrieval-api.
